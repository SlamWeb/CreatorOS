"""Local transformation and fault injection; live network research has a separate probe."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace

import httpx

from creatoros.integrations.codex import CodexProducer, CodexUsage
from creatoros.integrations.producer_skills import ProducerSkillCatalog, _digest, _write, skills_root_for
from creatoros.integrations.topic_research import TopicResearchService, ResearchReceipt
from creatoros.runs import ContentRunService
from creatoros.storage import Database, Series, PendingOperation, ContentRepository, upgrade_database
from creatoros.web import create_app
from tests.agent_studio_support import serve


def seed_batch(service, sid, batch_id="a" * 32):
    record = {"id": batch_id, "series_id": sid, "count": 2, "instructions": "", "status": "ready",
              "attempt": 1, "created_at": "2020-01-01T00:00:00+00:00", "snapshot": service.snapshot(sid),
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
        with serve(app) as base_url, httpx.Client(base_url=base_url, timeout=20, trust_env=False) as client:
            creator = client.post("/api/creators", json={"display_name": "隔离测试"}).json()
            sid = client.post(f"/api/creators/{creator['id']}/series", json={"name": "测试栏目"}).json()["id"]
            seed_batch(service, sid)
            snapshot = service.snapshot(sid)
            assert snapshot["creator"]["id"] == creator["id"]
            assert len(snapshot["existing_candidates"]) == 2 and not snapshot["existing_topics"]
            base = "/api/topic-research/" + "a" * 32
            assert client.get(base).json()["status"] == "ready"
            assert not repo.list_topics(sid)
            library = f"/api/series/{sid}/topic-library"
            initial = client.get(library).json()
            assert initial["page"]["total"] == 2
            first_id = initial["items"][0]["id"]
            assert initial["items"][0]["selection_state"] == "pending"
            assert client.get(library, params={"state": "queued"}).json()["page"]["total"] == 0
            assert client.get(library, params={"offset": 1, "limit": 1}).json()["items"][0]["candidate_id"] == "c2"
            assert client.get(library, params={"state": "invalid"}).status_code == 422
            assert client.get("/api/series/missing/topic-library").status_code == 404
            assert client.post("/api/runs", json={"topic_id": first_id}).status_code >= 400
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
            approved = client.get(library).json()
            assert approved["page"]["total"] == 2
            assert approved["items"][1]["id"] == first_id
            assert approved["items"][0]["title"] == "改后的标题"
            assert approved["items"][0]["sources"][0]["url"] == "https://docs.python.org/3/"
            assert client.get(library, params={"state": "pending"}).json()["page"]["total"] == 0
            run = runs.create(topics[0].id)
            assert run.input_snapshot_json["topic_brief"] == topics[0].brief
            prompt = CodexProducer.from_defaults()._build_prompt("c", sid, topics[0].id, topics[0].title,
                                                               topic_brief=run.input_snapshot_json["topic_brief"])
            assert "修改切入点" in prompt and "https://docs.python.org/3/" in prompt
            assert client.post(base + "/preview", json=select).status_code == 422
            assert all(c["queued"] for c in client.get(base).json()["candidates"])
            # Keep the old research snapshot while editing a real isolated working Skill.
            record = seed_batch(service, sid, "b" * 32)
            original_batch = service._path(record["id"]).read_bytes()
            local_skill = root / "local-skill"
            local_skill.mkdir()
            (local_skill / "SKILL.md").write_text(
                "---\nname: research-carousel\ndescription: Local test\n"
                "creatoros-output: social-content-pack.image-carousel\n---\nOriginal rules.\n",
                encoding="utf-8")
            skill = service.catalog.register_local(local_skill, role="production")
            with db.session() as session:
                session.get(Series, sid).skill_name = skill["id"]
            newer = "/api/topic-research/" + "b" * 32
            assert not client.get(newer).json()["stale"]
            pending = client.post(newer + "/preview", json={"selections": [{"candidate_id": "c1"}]}).json()
            # Scope change after preview must be caught at transaction-time confirmation.
            with db.session() as session:
                session.get(Series, sid).audience = "changed"
                session.get(Series, sid).description = "current positioning"
            skill_file = service.catalog.resolve(skill["id"]) / "SKILL.md"
            previous_digest = _digest(skill_file.parent)
            skill_file.write_text(skill_file.read_text(encoding="utf-8") + "Current rules.\n", encoding="utf-8")
            current_digest = _digest(skill_file.parent)
            assert current_digest != previous_digest and current_digest != record["snapshot"]["skill_digest"]
            assert not client.get(newer).json()["stale"]
            pending_rows = client.get(library, params={"state": "pending"}).json()["items"]
            assert len(pending_rows) == 2 and all(not r["stale"] and r["available_actions"] for r in pending_rows)
            assert all(r["research_created_at"].startswith("2020-") for r in pending_rows)
            assert client.get(library, params={"state": "queued"}).json()["items"][0]["title"] == "改后的标题"
            body = {"expected_version": pending["version"], "expected_revision": pending["revision"],
                    "confirmation_token": pending["confirmation_token"]}
            conflict = client.post(f"/api/operations/{pending['id']}/confirm", json=body)
            assert conflict.status_code == 409, conflict.text
            assert "重新预览" in client.get(f"/api/operations/{pending['id']}").json()["message"]
            assert len(repo.list_topics(sid)) == 2
            fresh = client.post(newer + "/preview", json={"selections": [{"candidate_id": "c1"}]})
            assert fresh.status_code == 200, fresh.text
            fresh = fresh.json()
            with db.session() as session:
                expected = session.get(PendingOperation, fresh["id"]).plan_json["operations"][0]["expected_series"]
            assert expected["audience"] == "changed" and expected["description"] == "current positioning"
            assert expected["skill_name"] == skill["id"]
            body = {"expected_version": fresh["version"], "expected_revision": fresh["revision"],
                    "confirmation_token": fresh["confirmation_token"]}
            confirm_url = f"/api/operations/{fresh['id']}/confirm"
            assert client.post(confirm_url, json={**body, "expected_version": body["expected_version"] + 1}).status_code == 409
            assert client.post(confirm_url, json=body).status_code == 200
            assert client.post(confirm_url, json=body).status_code == 200
            historical_run = runs.create(service.topic_id(record["id"], "c1"))
            assert historical_run.input_snapshot_json["audience"] == "changed"
            assert historical_run.input_snapshot_json["series_description"] == "current positioning"
            assert historical_run.input_snapshot_json["skill_name"] == skill["id"]
            assert historical_run.input_snapshot_json["skill_digest"] == current_digest
            direct = {"selections": [{"candidate_id": "c2"}], "request_id": "research-direct-b-c2"}
            queued = client.post(newer + "/queue", json=direct)
            assert queued.status_code == 201 and not queued.json()["deduplicated"], queued.text
            assert client.post(newer + "/queue", json={**direct, "request_id": "research-duplicate-b-c2"}).status_code == 422
            assert len(repo.list_topics(sid)) == 4
            assert service._path(record["id"]).read_bytes() == original_batch
            # Inactive columns remain unavailable; this is a current-state guard, not age expiry.
            with db.session() as session:
                session.get(Series, sid).is_active = False
            assert client.get(newer).json()["stale"]
            inactive = client.post(newer + "/preview", json={"selections": [{"candidate_id": "c1"}]})
            assert inactive.status_code == 422 and "栏目不存在或已停用" in inactive.text
            with db.session() as session:
                session.get(Series, sid).is_active = True
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
            assert 'model="gpt-6-luna"' in command and 'model_reasoning_effort="xhigh"' in command
        db.close()
    print("topic_research_smoke=passed preview=readonly provenance=passed duplicate=blocked durable_candidates=passed preview_conflict=blocked current_run_input=passed config_refresh=passed restart=passed")


if __name__ == "__main__":
    main()
