"""Real isolated SQLite/loopback checks; seeded task facts, no LLM or image generation."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from fastapi import FastAPI
import httpx
from PIL import Image
from sqlalchemy import func, select

from creatoros.integrations.producer_skills import ProducerSkillCatalog, SkillInstallService, skills_root_for
from creatoros.integrations.skill_iterations import SkillIterationService
from creatoros.runs import ContentRunError, ContentRunService
from creatoros.runs.queue import ProductionQueue
from creatoros.storage import (ContentRepository, ContentRun, ContentRunStatus, CreatorPlatform,
    Database, Series, SkillIteration, Topic, TopicRemoval, TopicSource, upgrade_database)
from creatoros.web.app import _error_response, create_app
from creatoros.web.worker_tasks import worker_task_routes
from tests.agent_studio_support import serve


class RsiProductionGuardTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        url = f"sqlite:///{(self.root / 'rsi-guard.db').as_posix()}"
        upgrade_database(url)
        self.db = Database(url)
        self.addCleanup(self.db.close)
        self.catalog = ProducerSkillCatalog(skills_root_for(self.db))
        source = self.root / "local-skill"
        source.mkdir()
        (source / "SKILL.md").write_text(
            "---\nname: rsi-guard-fixture\ndescription: Local state guard fixture\n"
            "creatoros-output: social-content-pack.image-carousel\n---\nKeep the supplied copy.\n",
            encoding="utf-8")
        skill = self.catalog.register_local(source, role="legacy_end_to_end")["id"]
        repository = ContentRepository(self.db)
        for creator in ("a", "b"):
            repository.create_creator(creator_id=creator, display_name=creator,
                                      platform=CreatorPlatform.XIAOHONGSHU)
        repository.create_series(series_id="series", creator_id="a", name="栏目",
            description="", audience="", skill_name=skill)
        for identifier in ("topic", "another"):
            repository.add_topic(topic_id=identifier, series_id="series", title=identifier,
                                 source=TopicSource.MANUAL)
        self.runs = ContentRunService(self.db, output_root=self.root / "outputs")
        self.iterations = SkillIterationService(self.db,
            SimpleNamespace(catalog=self.catalog, extractions=None), chat=None)
        self.identity = sha256(b"isolated-rsi-owner").hexdigest()

    def seed_iteration(self, status="waiting_production"):
        with self.db.session() as session:
            session.add(SkillIteration(id=self.identity, request_id=self.identity, input_digest="a" * 64,
                creator_id="a", series_id="series", skill_id="fixture-skill", topic_id="topic", status=status,
                config_json={"creator_id": "a", "series_id": "series", "series_name": "栏目",
                    "topic_id": "topic", "topic_title": "原选题", "skill_name": "图文方法", "references": []},
                state_json={"phase": "waiting", "max_rounds": 3,
                    "rounds": [{"number": 1, "status": "running", "trial_job_id": "actual-job",
                                "trial_id": "actual-trial", "trial_url": "/skills?extraction=actual-job", "images": []}],
                    "judge": {"version": 1}, "usage": {}, "events": [], "calls": {}}))

    def assert_blocked(self, action):
        with self.assertRaises(ContentRunError) as caught:
            action()
        self.assertEqual(caught.exception.code, "skill_iteration_active")
        self.assertEqual(caught.exception.iteration_id, self.identity)

    def test_active_rsi_blocks_new_run_but_not_another_topic_and_does_not_mutate_task(self):
        for status in ("queued", "running", "waiting_production"):
            with self.subTest(status=status):
                if status == "queued":
                    self.seed_iteration(status)
                else:
                    with self.db.session() as session:
                        session.get(SkillIteration, self.identity).status = status
                before = self.iterations.get(self.identity)
                self.assert_blocked(lambda: self.runs.create("topic"))
                self.assertEqual(self.iterations.get(self.identity), before)
        self.assertEqual(self.runs.create("another").topic_id, "another")
        with self.db.session() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(ContentRun)), 1)

    def test_active_rsi_blocks_fresh_execution_authorization_and_direct_claim(self):
        source = self.runs.create("topic")
        with self.db.session() as session:
            session.get(ContentRun, source.id).status = ContentRunStatus.FAILED
            session.get(ContentRun, source.id).retryable = True
        source = self.runs.get(source.id)
        self.seed_iteration()
        self.assert_blocked(lambda: self.runs.restart_fresh(source.id,
            expected_version=source.version, request_id="no-duplicate"))
        self.assert_blocked(lambda: ProductionQueue(self.runs).enqueue(source.id, source.version))
        self.assert_blocked(lambda: self.runs._begin_attempt(source.id, owner_id="must-not-run"))
        self.assertEqual(self.runs.create("topic").id, source.id)  # Pure idempotent read is safe.
        self.assertFalse(self.runs.repository.list_attempts(self.runs.get_active_revision(source.id).id))

    def test_ended_rsi_never_blocks_new_production(self):
        self.seed_iteration("failed")
        run = self.runs.create("topic")
        for status in ("completed", "exhausted", "paused", "interrupted", "failed"):
            with self.subTest(status=status):
                with self.db.session() as session:
                    session.get(SkillIteration, self.identity).status = status
                    if status == "paused":
                        row = session.get(SkillIteration, self.identity)
                        state = deepcopy(row.state_json)
                        state["rounds"][-1]["status"] = "completed"
                        row.state_json = state
                    session.get(ContentRun, run.id).status = ContentRunStatus.FAILED
                run = self.runs.get(run.id)
                run = self.runs.restart_fresh(run.id, expected_version=run.version, request_id="ended-" + status)
                self.assertEqual(run.status, ContentRunStatus.QUEUED)

    def test_pausing_does_not_release_inflight_trial_and_terminal_metadata_does(self):
        self.seed_iteration("paused")
        self.assert_blocked(lambda: self.runs.create("topic"))
        self.runs.iteration_trial_active = lambda job, trial: False
        self.assertEqual(self.runs.create("topic").topic_id, "topic")

    def test_changed_owner_blocks_without_exposing_foreign_iteration_pointer(self):
        self.seed_iteration()
        with self.db.session() as session:
            session.get(Series, "series").creator_id = "b"
        with self.assertRaises(ContentRunError) as caught:
            self.runs.create("topic")
        self.assertEqual(caught.exception.code, "topic_scope_changed")
        self.assertIsNone(caught.exception.iteration_id)

    def test_fresh_reads_current_topic_copy_and_preserves_old_snapshots(self):
        with self.db.session() as session:
            session.get(Topic, "topic").brief = "旧文案：别再用它。"
        source = self.runs.create("topic")
        original = deepcopy(source.input_snapshot_json)
        revision = self.runs.get_active_revision(source.id)
        original_revision = deepcopy(revision.production_input_json)
        with self.db.session() as session:
            session.get(ContentRun, source.id).status = ContentRunStatus.FAILED
            topic = session.get(Topic, "topic")
            topic.title, topic.brief = "同一选题的新标题", "25岁，在村口讨饭；40岁，坐在南京殿上受百官朝贺。"
        source = self.runs.get(source.id)
        fresh = self.runs.restart_fresh(source.id, expected_version=source.version, request_id="edited-original")
        self.assertEqual(fresh.topic_id, source.topic_id)
        self.assertEqual(fresh.input_snapshot_json["topic_title"], "同一选题的新标题")
        self.assertNotIn("旧文案", fresh.input_snapshot_json["topic_brief"])
        self.assertEqual(self.runs.get(source.id).input_snapshot_json, original)
        self.assertEqual(self.runs.get_active_revision(source.id).production_input_json, original_revision)
        with self.db.session() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Topic)), 2)

    def test_real_loopback_task_query_and_error_pointer_are_read_only_and_scope_filtered(self):
        self.seed_iteration()
        app = FastAPI()
        app.include_router(worker_task_routes(self.db,
            SimpleNamespace(list=lambda _: []), SimpleNamespace(list_for_creator=lambda *a, **k: []),
            iterations=self.iterations))

        @app.post("/probe-production")
        def production():
            self.runs.create("topic")

        @app.exception_handler(ContentRunError)
        async def error_handler(request, error):
            return _error_response(error.status_code, error.code, str(error), iteration_id=error.iteration_id)

        before = self.iterations.get(self.identity)
        with serve(app) as base, httpx.Client(base_url=base, timeout=10) as client:
            response = client.get("/api/creators/a/tasks", params={"statuses": "waiting_production"})
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["summary"]["active"], 1)
            self.assertEqual(len(body["items"]), 1)
            item = body["items"][0]
            self.assertEqual((item["kind"], item["iteration_id"], item["stage"]),
                             ("skill_iteration", self.identity, "waiting"))
            self.assertTrue(item["active_round"]["production_submitted"])
            self.assertEqual(item["active_round"]["production_status"], "running")
            self.assertEqual(item["next_action"], "backend_waits_for_production")
            self.assertEqual(client.get("/api/creators/b/tasks").json()["items"], [])
            blocked = client.post("/probe-production")
            self.assertEqual(blocked.status_code, 409)
            self.assertEqual(blocked.json()["error"]["url"], f"/skills?iteration={self.identity}")
            self.assertEqual(blocked.json()["error"]["iteration_id"], self.identity)
            with self.db.session() as session:
                session.get(Series, "series").creator_id = "b"
            self.assertEqual(client.get("/api/creators/a/tasks").json()["items"], [])
            self.assertEqual(client.get("/api/creators/b/tasks").json()["items"], [])
        self.assertEqual(self.iterations.get(self.identity), before)

    def test_original_batch_route_keeps_rsi_pointer_without_adding_execution_authorization(self):
        source = self.runs.create("topic")
        app = create_app(database=self.db, run_service=self.runs,
            skill_install_service=SkillInstallService(self.catalog))
        with serve(app) as base, httpx.Client(base_url=base, timeout=10) as client:
            # This regression tests submission, not a paid worker. Stop the real
            # isolated RSI worker before seeding its already-in-flight task fact.
            app.state.skill_iterations.shutdown()
            before = client.get("/api/topics/topic").json()
            old_edit = {"expected_digest": before["edit_digest"], "request_id": "edit-before-rsi",
                        "brief": "已成功保存的旧修改"}
            self.assertEqual(client.patch("/api/topics/topic", json=old_edit).status_code, 200)
            self.seed_iteration()
            response = client.post("/api/runs/batch", json={"topic_ids": ["topic"]})
            self.assertEqual(response.status_code, 202, response.text)
            body = response.json()
            self.assertEqual(body["accepted_count"], 0)
            item = body["items"][0]
            self.assertEqual(item["error_code"], "skill_iteration_active")
            self.assertEqual(item["run_id"], source.id)
            self.assertEqual(item["iteration_id"], self.identity)
            self.assertEqual(item["url"], f"/skills?iteration={self.identity}")
            self.assertIsNone(ProductionQueue(self.runs).snapshot(source.id))
            self.assertFalse(self.runs.repository.list_attempts(self.runs.get_active_revision(source.id).id))
            for method, path, body in (
                ("PATCH", "/api/topics/topic", {"brief": "不能覆盖正在校准的选题"}),
                ("POST", "/api/topics/topic/remove", {"request_id": "no-rsi-removal"}),
                ("POST", "/api/topics/topic/delete", {}),
            ):
                protected = client.request(method, path, json=body)
                self.assertEqual(protected.status_code, 409, protected.text)
                self.assertIn("RSI", protected.json()["error"]["message"])
            with self.db.session() as session:
                self.assertEqual(session.get(Topic, "topic").brief, "已成功保存的旧修改")
            view = client.get("/api/topics/topic").json()
            self.assertEqual(view["management_actions"], ["get"])
            replay = client.patch("/api/topics/topic", json=old_edit)
            self.assertEqual(replay.status_code, 200, replay.text)
            self.assertTrue(replay.json()["deduplicated"])
            self.assertEqual(client.get("/api/topics/topic").json(), view)

    def test_finished_rsi_without_ordinary_run_is_archived_and_old_record_image_remain_readable(self):
        self.seed_iteration("completed")
        buffer = BytesIO()
        Image.new("RGB", (4, 4), "#5196e3").save(buffer, format="PNG")
        raw = buffer.getvalue()
        image = self.iterations._path(self.identity) / "references" / "old.png"
        image.parent.mkdir(parents=True)
        image.write_bytes(raw)
        with self.db.session() as session:
            row = session.get(SkillIteration, self.identity)
            config = deepcopy(row.config_json)
            config["references"] = [{"key": "ref_old", "file": "old.png", "name": "原参考",
                                     "sha256": sha256(raw).hexdigest(), "mime_type": "image/png"}]
            row.config_json = config
        with self.db.session() as session:
            row = session.get(SkillIteration, self.identity)
            original = {column.name: deepcopy(getattr(row, column.name)) for column in row.__table__.columns}
        app = create_app(database=self.db, run_service=self.runs,
            skill_install_service=SkillInstallService(self.catalog))
        with serve(app) as base, httpx.Client(base_url=base, timeout=10) as client:
            sid = app.state.chat.create("a")["id"]
            headers = {"x-creatoros-agent-session": sid}
            route = f"/api/skill-iterations/{self.identity}"
            before = client.get(route, headers=headers)
            self.assertEqual(before.status_code, 200, before.text)
            self.assertEqual(client.post("/api/topics/topic/delete", json={}).status_code, 409)
            removed = client.post("/api/topics/topic/remove", json={"request_id": "archive-finished-rsi"})
            self.assertEqual(removed.status_code, 200, removed.text)
            self.assertEqual(removed.json()["status"], "archived")
            after = client.get(route, headers=headers)
            self.assertEqual(after.status_code, 200, after.text)
            self.assertEqual(after.json(), before.json())
            old_image = client.get(route + "/files/ref_old", headers=headers)
            self.assertEqual(old_image.status_code, 200, old_image.text)
            self.assertEqual(old_image.content, raw)
            self.assertEqual(image.read_bytes(), raw)
            with self.db.session() as session:
                self.assertIsNotNone(session.get(Topic, "topic"))
                self.assertEqual(session.get(TopicRemoval, "topic").status, "archived")
                current = session.get(SkillIteration, self.identity)
                self.assertEqual({column.name: deepcopy(getattr(current, column.name))
                    for column in current.__table__.columns}, original)
                self.assertEqual(session.scalar(select(func.count()).select_from(ContentRun)), 0)


if __name__ == "__main__":
    unittest.main()
