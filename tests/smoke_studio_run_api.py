from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from threading import Event

from fastapi.testclient import TestClient
from PIL import Image

from creatoros.content import CarouselCard, PublicationCopy, SocialContentPack
from creatoros.integrations.codex import CodexUsage, ProducedPack, ProductionSession
from creatoros.runs import ContentRunService, ManagedRunExecutor
from creatoros.storage import CreatorPlatform, Database, ContentRepository, TopicSource, upgrade_database
from creatoros.web.app import create_app


class ApiProducer:
    def __init__(self):
        self.started = Event()
        self.release = Event()

    def produce_to(self, **request) -> ProducedPack:
        request["on_thread_started"]("api-thread")
        self.started.set()
        self.release.wait(timeout=5)
        assert request["series_description"] == "desc" and request["audience"] == "audience"
        directory = Path(request["directory"])
        directory.mkdir(parents=True, exist_ok=False)
        image_path = directory / "images" / "01-cover.png"
        image_path.parent.mkdir()
        Image.new("RGB", (320, 480), "#f2eee7").save(image_path)
        pack = SocialContentPack(
            pack_id=request["pack_id"], creator_id=request["creator_id"], series_id=request["series_id"],
            topic_id=request["topic_id"], topic_title=request["topic_title"], skill_name="knowledge-to-carousel",
            generated_at="2026-09-04T12:00:00+08:00", content_summary="API smoke",
            cards=[CarouselCard(order=1, kind="cover", headline="API", image_path="images/01-cover.png")],
            publish_copy=PublicationCopy(title="API", body="Smoke"),
        )
        (directory / "social_content_pack.json").write_text(pack.model_dump_json(), encoding="utf-8")
        session = ProductionSession(
            thread_id="api-thread", pack_id=request["pack_id"], created_at="2026-09-04T12:00:00+08:00",
            usage=CodexUsage(input_tokens=1, cached_input_tokens=0, output_tokens=1),
        )
        (directory / "production_session.json").write_text(session.model_dump_json(), encoding="utf-8")
        return ProducedPack(directory=directory, pack=pack, session=session)


with TemporaryDirectory() as temporary:
    root = Path(temporary)
    url = f"sqlite:///{(root / 'api.db').as_posix()}"
    upgrade_database(url)
    database = Database(url)
    content = ContentRepository(database)
    content.create_creator(creator_id="creator-1", display_name="One", platform=CreatorPlatform.XIAOHONGSHU)
    content.create_series(series_id="series-1", creator_id="creator-1", name="Basics", description="desc", audience="audience", skill_name="knowledge-to-carousel")
    content.add_topic(topic_id="topic-1", series_id="series-1", title="Topic 1", source=TopicSource.MANUAL)
    content.add_topic(topic_id="topic-2", series_id="series-1", title="Topic 2", source=TopicSource.MANUAL)
    producer = ApiProducer()
    service = ContentRunService(database, producer_factory=lambda: producer, output_root=root / "outputs")
    executor = ManagedRunExecutor(service)
    app = create_app(database=database, run_service=service, run_executor=executor)
    with TestClient(app) as client:
        first = client.post("/api/runs", json={"topic_id": "topic-1"})
        assert first.status_code == 201, first.text
        run_id = first.json()["id"]
        assert first.json()["status"] == "queued"
        duplicate = client.post("/api/runs", json={"topic_id": "topic-1"})
        assert duplicate.status_code == 200 and duplicate.json()["id"] == run_id
        forbidden_key = client.post("/api/runs", json={"topic_id": "topic-1", "idempotency_key": "another"})
        assert forbidden_key.status_code == 422
        stale = client.post(f"/api/runs/{run_id}/execute", json={"expected_version": 999})
        assert stale.status_code == 409
        submitted_at = monotonic()
        started = client.post(f"/api/runs/{run_id}/execute", json={"expected_version": first.json()["version"]})
        assert started.status_code == 202, started.text
        assert monotonic() - submitted_at < 2 and producer.started.wait(1)
        assert client.get("/api/overview").json()["counts"]["producing_count"] == 1
        second = client.post("/api/runs", json={"topic_id": "topic-2"}).json()
        busy = client.post(f"/api/runs/{second['id']}/execute", json={"expected_version": second["version"]})
        assert busy.status_code == 409 and busy.json()["error"]["code"] == "producer_busy"
        assert busy.json()["error"]["run_id"] == run_id
        assert client.get("/api/series/series-1").json()["available_topic_count"] == 1
        denied = client.post("/api/runs", json={"topic_id": "topic-2"}, headers={"Origin": "https://external.example"})
        assert denied.status_code == 403
        created_series = client.post("/api/creators/creator-1/series", json={"name": "During production", "description": "Test", "audience": "Beginners"})
        assert created_series.status_code == 201
        producer.release.set()
        deadline = monotonic() + 5
        status = "queued"
        while monotonic() < deadline:
            detail = client.get(f"/api/runs/{run_id}").json()
            status = detail["status"]
            if status == "awaiting_approval":
                break
            sleep(0.02)
        assert status == "awaiting_approval", status
        assert len(detail["revisions"][0]["attempts"]) == 1
        late_duplicate = client.post("/api/runs", json={"topic_id": "topic-1"})
        assert late_duplicate.status_code == 200 and late_duplicate.json()["id"] == run_id
        sleep(0.05)
        assert len(client.get(f"/api/runs/{run_id}").json()["revisions"][0]["attempts"]) == 1
        assert client.get("/api/overview").json()["counts"]["awaiting_approval_count"] == 1
    database.close()

print("studio_run_api_smoke=passed submit=202 duplicate=idempotent poll=approval")
