"""Task reference research: real files/HTTP/SQLite; controlled SDK, no model fee.

The controlled SDK verifies transport and failure boundaries, not research quality.
Real Codex/Web validation is run separately against an isolated workspace.
"""
import base64
import io
import json
import os
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from PIL import Image

from creatoros.integrations.codex import CodexUsage
from creatoros.integrations.producer_skills import ProducerSkillCatalog, skills_root_for
from creatoros.integrations.topic_references import TopicReferenceStore
from creatoros.integrations.topic_research import CandidateSelection, CodexTopicResearcher, ResearchReceipt, TopicResearchService
from creatoros.runs import ContentRunService
from creatoros.storage import ContentRepository, Database, Series, upgrade_database
from creatoros.web import create_app
from tests.agent_studio_support import serve
from tests.smoke_topic_research_sdk import ControlledSdk


def png(color):
    stream = io.BytesIO()
    Image.new("RGB", (24, 24), color).save(stream, format="PNG")
    return stream.getvalue()


def main():
    with TemporaryDirectory(prefix="reference-research-") as temporary, ExitStack() as cleanup:
        root = Path(temporary)
        database_url = f"sqlite:///{(root / 'test.db').as_posix()}"
        upgrade_database(database_url)
        database = Database(database_url)
        cleanup.callback(database.close)
        catalog = ProducerSkillCatalog(skills_root_for(database))
        researcher = CodexTopicResearcher(timeout_seconds=5)
        service = TopicResearchService(database, catalog, researcher=researcher)
        runs = ContentRunService(database, output_root=root / "outputs")
        app = create_app(database=database, run_service=runs, topic_research_service=service)
        repository = ContentRepository(database)
        references = TopicReferenceStore(database)
        raw_images = [png("orange"), png("blue")]
        with serve(app) as base_url, httpx.Client(base_url=base_url, trust_env=False, timeout=20) as client:
            creator = client.post("/api/creators", json={"display_name": "参考作品隔离账号"}).json()
            series = client.post(f"/api/creators/{creator['id']}/series", json={"name": "英语栏目"}).json()["id"]
            other = client.post(f"/api/creators/{creator['id']}/series", json={"name": "另一个栏目"}).json()["id"]
            image_ids = [references.upload(series, f"中文原图-{i}.png", base64.b64encode(data).decode())["id"]
                         for i, data in enumerate(raw_images, 1)]
            instruction = "借鉴这两张作品关于害怕的切入点，讲常考英语；不要只留一个标题。"
            for presentation in (False, True):
                ref = references.freeze(series, image_ids, instruction, presentation)
                original_files = {Path(image.path): Path(image.path).read_bytes() for image in ref.images}
                sdk = ControlledSdk("success", service.cancel)
                with patch.dict(os.environ, {"CREATOROS_CODEX_EXECUTABLE": ""}), \
                     patch("openai_codex.AsyncCodex", side_effect=sdk.build):
                    response = client.post(f"/api/series/{series}/topic-research", json={
                        "count": 1, "instructions": "请联网核实", "reference_id": ref.id})
                    assert response.status_code == 202, response.text
                    service.worker.join(8)
                    assert not service.worker.is_alive()
                job = client.get(f"/api/topic-research/{response.json()['id']}").json()
                assert job["status"] == "ready", job
                assert job["reference_id"] == ref.id and job["candidates"][0]["reference_id"] == ref.id
                assert job["reference_summary"] == {"image_count": 2, "reference_presentation": presentation}
                assert "task_references" not in job and str(root) not in json.dumps(job, ensure_ascii=False)
                record = service._load(job["id"])
                assert record["task_references"] == ref.model_dump(mode="json")
                assert record["snapshot"]["task_references"] == record["task_references"]
                inputs, _kwargs = sdk.turn_calls[0]
                assert len(inputs) == 3 and instruction in inputs[0].text and "请联网核实" in inputs[0].text
                assert ("同时借鉴内容与呈现" if presentation else "只借鉴内容") in inputs[0].text
                assert "不执行 Skill" in inputs[0].text
                received_images = [Path(item.path) for item in inputs[1:]]
                assert [image.read_bytes() for image in received_images] == raw_images
                assert all(image.parent.name == "references" for image in received_images)
                assert all(image.is_relative_to(service.root / "work" / job["id"]) for image in received_images)
                assert all(path.read_bytes() == content for path, content in original_files.items())
                _record, plan = service._draft_plan(job["id"], [CandidateSelection(candidate_id="c1")])
                assert plan.operations[0].topics[0].reference_id == ref.id
                assert not repository.list_topics(series), "research never silently queues or produces"
                assert not any(catalog.root.glob("working/**/reference-*.png")), "references cannot pollute shared Skills"
                foreign = client.post(f"/api/series/{other}/topic-research", json={"count": 1, "reference_id": ref.id})
                assert foreign.status_code == 422, foreign.text
                assert "local-path" not in foreign.text and str(root) not in foreign.text

            # A configuration restart must carry the exact task input, while an
            # in-flight same request reuses the existing batch (not a second call).
            started, release = Event(), Event()
            captured = []

            class BlockingResearch:
                def research(self, snapshot, count, instructions, workspace, cancel):
                    captured.append(snapshot)
                    if len(captured) == 1:
                        started.set()
                        assert release.wait(8)
                    return SimpleNamespace(thread_id="controlled-config-race", usage=CodexUsage(),
                                           receipt=ResearchReceipt(candidates=[], note="controlled config test"))

            service.researcher = BlockingResearch()
            ref = references.freeze(series, image_ids, instruction, False)
            job = service.submit(series, 1, "同一要求", reference_id=ref.id)
            assert started.wait(5)
            try:
                assert service.submit(series, 1, "同一要求", reference_id=ref.id)["id"] == job["id"]
                different = references.freeze(series, image_ids, instruction, True)
                try:
                    service.submit(series, 1, "同一要求", reference_id=different.id)
                except ValueError as error:
                    assert "进行中" in str(error)
                else:
                    raise AssertionError("different references must not reuse an active research batch")
                with database.session() as session:
                    session.get(Series, series).description = "新的栏目定位"
            finally:
                release.set()
                service.worker.join(8)
            assert service.get(job["id"])["status"] == "ready"
            assert len(captured) == 2
            assert all(snapshot["task_references"] == ref.model_dump(mode="json") for snapshot in captured)
            assert captured[1]["series"]["description"] == "新的栏目定位"
            # No-reference clients still receive the pre-existing shape.
            legacy = service.submit(series, 1)
            service.worker.join(8)
            legacy = service.get(legacy["id"])
            assert legacy["status"] == "ready" and "reference_id" not in legacy
            assert "task_references" not in captured[-1]
        database.close()
    print("reference_research=passed originals/order/SDK-multimodal/intent/presentation/scope/projection/candidate-provenance/config-refresh/idempotency/legacy")


if __name__ == "__main__":
    main()
