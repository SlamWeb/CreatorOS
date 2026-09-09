"""Local transformation and fault injection; live network research has a separate probe."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace

from fastapi.testclient import TestClient

from creatoros.integrations.codex import CodexProducer, CodexUsage
from creatoros.integrations.producer_skills import ProducerSkillCatalog, _write, skills_root_for
from creatoros.integrations.topic_research import TopicResearchService, ResearchReceipt
from creatoros.runs import ContentRunService
from creatoros.storage import Database, Series, ContentRepository, upgrade_database
from creatoros.web import create_app


def seed_batch(service, sid, batch_id="a" * 32):
    record = {"id": batch_id, "series_id": sid, "count": 2, "instructions": "", "status": "ready",
              "attempt": 1, "created_at": "2026-09-09T00:00:00+00:00", "snapshot": service.snapshot(sid),
              "candidates": [{"id": "c1", "title": "工具调用", "angle": "为何使用 Schema", "rationale": "适合初学者",
                              "sources": [{"title": "官方文档", "url": "https://platform.openai.com/docs/guides/function-calling"}]},
                             {"id": "c2", "title": "上下文", "angle": "如何压缩", "rationale": "面试讲清机制",
                              "sources": [{"title": "Python", "url": "https://docs.python.org/3/"}]}],
              "note": "Local fixture, not a research claim", "attempts": []}
    _write(service._path(batch_id), record)
    return record


def main():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        url = f"sqlite:///{(root / 'test.db').as_posix()}"
        upgrade_database(url)
        db = Database(url)
        service = TopicResearchService(db, ProducerSkillCatalog(skills_root_for(db)))
        runs = ContentRunService(db, output_root=root / "outputs")
        app = create_app(database=db, run_service=runs, topic_research_service=service)
        repo = ContentRepository(db)
        with TestClient(app) as client:
            creator = client.post("/api/creators", json={"display_name": "隔离测试"}).json()
            sid = client.post(f"/api/creators/{creator['id']}/series", json={"name": "测试栏目"}).json()["id"]
            seed_batch(service, sid)
            base = "/api/topic-research/" + "a" * 32
            assert client.get(base).json()["status"] == "ready"
            assert not repo.list_topics(sid)
            select = {"selections": [{"candidate_id": "c2", "title": "改后的标题", "angle": "修改切入点"}, {"candidate_id": "c1"}]}
            response = client.post(base + "/preview", json=select)
            assert response.status_code == 200, response.text
            operation = response.json()
            assert not repo.list_topics(sid)
            assert client.post(base + "/preview", json={"selections": [{"candidate_id": "c9"}]}).status_code == 422
            assert client.post(base + "/preview", json={"selections": [{"candidate_id": "c1"}] * 2}).status_code == 422
            payload = {"expected_version": operation["version"], "expected_revision": operation["revision"],
                       "confirmation_token": operation["confirmation_token"]}
            confirmed = client.post(f"/api/operations/{operation['id']}/confirm", json=payload)
            assert confirmed.status_code == 200, confirmed.text
            assert client.post(f"/api/operations/{operation['id']}/confirm", json=payload).status_code == 200
            topics = repo.list_topics(sid)
            assert [t.title for t in topics] == ["改后的标题", "工具调用"]
            assert "修改切入点" in topics[0].brief and "https://docs.python.org/3/" in topics[0].brief
            run = runs.create(topics[0].id)
            assert run.input_snapshot_json["topic_brief"] == topics[0].brief
            prompt = CodexProducer.from_defaults()._build_prompt("c", sid, topics[0].id, topics[0].title,
                                                               topic_brief=run.input_snapshot_json["topic_brief"])
            assert "修改切入点" in prompt and "https://docs.python.org/3/" in prompt
            assert client.post(base + "/preview", json=select).status_code == 422
            assert all(c["queued"] for c in client.get(base).json()["candidates"])
            seed_batch(service, sid, "b" * 32)
            newer = "/api/topic-research/" + "b" * 32
            pending = client.post(newer + "/preview", json={"selections": [{"candidate_id": "c1"}]}).json()
            # Scope change after preview must be caught at transaction-time confirmation.
            with db.session() as session:
                session.get(Series, sid).audience = "changed"
            assert client.get(newer).json()["stale"]
            assert client.post(newer + "/preview", json={"selections": [{"candidate_id": "c1"}]}).status_code == 422
            body = {"expected_version": pending["version"], "expected_revision": pending["revision"],
                    "confirmation_token": pending["confirmation_token"]}
            assert client.post(f"/api/operations/{pending['id']}/confirm", json=body).status_code == 409
            assert len(repo.list_topics(sid)) == 2
            # Fault injection: settings change during a research call; second call sees latest.
            started, release = Event(), Event()
            snapshots = []
            class ControlledResearch:
                def research(self, snapshot, count, instructions, workspace, cancel):
                    snapshots.append(snapshot)
                    if len(snapshots) == 1:
                        started.set()
                        assert release.wait(5)
                    return SimpleNamespace(receipt=ResearchReceipt(candidates=[], note="Local config-race test"),
                                           thread_id="local-fault-test", usage=CodexUsage())
            service.researcher = ControlledResearch()
            job = service.submit(sid, 1)
            assert started.wait(5)
            assert service.submit(sid, 1)["id"] == job["id"]
            with db.session() as session:
                session.get(Series, sid).description = "new positioning"
            release.set()
            service.worker.join(8)
            assert service.get(job["id"])["status"] == "ready"
            assert len(snapshots) == 2 and snapshots[1]["series"]["description"] == "new positioning"
            # Abandoned jobs never silently re-launch (potentially paid) research on restart.
            abandoned = seed_batch(service, sid, "c" * 32)
            abandoned["status"] = "researching"
            _write(service._path(abandoned["id"]), abandoned)
            service.start()
            assert service.get(abandoned["id"])["status"] == "interrupted"
        for thread in [None, "test-thread"]:
            command = CodexProducer.from_defaults()._command(root / "schema.json", root, thread)
            assert 'model="gpt-5.6-luna"' in command and 'model_reasoning_effort="xhigh"' in command
        db.close()
    print("topic_research_smoke=passed preview=readonly provenance=passed duplicate=blocked stale=blocked config_refresh=passed restart=passed")


if __name__ == "__main__":
    main()
