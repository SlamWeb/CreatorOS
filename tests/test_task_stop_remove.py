"""Explicit stop/archive regressions with isolated SQLite and controlled producers."""
from pathlib import Path
from unittest import TestCase

from creatoros.runs import ContentRunError
from creatoros.storage import ContentRun, ProductionTask
from tests import test_production_queue as queue_fixture
from tests import test_simple_skill_tasks as skill_fixture


class StopRunningTests(TestCase):
    setUp = queue_fixture.ProductionQueueTests.setUp
    tearDown = queue_fixture.ProductionQueueTests.tearDown
    service = queue_fixture.ProductionQueueTests.service
    executor = queue_fixture.ProductionQueueTests.executor

    def test_stop_signals_worker_preserves_partial_and_next_task_runs(self):
        producer = queue_fixture.Producer(block=True)
        service = self.service(producer)
        executor = self.executor(service)
        first, second = service.create("topic-0"), service.create("topic-1")
        executor.submit(first.id, expected_version=first.version)
        self.assertTrue(producer.started.wait(3))
        executor.submit(second.id, expected_version=second.version)
        running = service.get(first.id)
        self.assertEqual(running.status.value, "producing")
        with self.assertRaises(ContentRunError):
            executor.cancel(first.id, expected_version=running.version + 1)
        self.assertFalse(executor._active[first.id].cancel.is_set())
        executor.cancel(first.id, expected_version=running.version)
        queue_fixture.wait(lambda: service.get(first.id).status.value == "interrupted", timeout=10)
        queue_fixture.wait(lambda: service.get(second.id).status.value == "awaiting_approval", timeout=10)
        attempts = service.repository.list_attempts(service.get_active_revision(first.id).id)
        self.assertEqual(len(attempts), 1)
        self.assertTrue((Path(attempts[0].output_directory) / "images/01.png").is_file())
        self.assertEqual(producer.calls, ["topic-0", "topic-1"])


class ArchiveTasksTests(TestCase):
    skill = skill_fixture.SimpleSkillTasksTests.skill
    setUp = skill_fixture.SimpleSkillTasksTests.setUp
    tearDown = skill_fixture.SimpleSkillTasksTests.tearDown

    def test_archive_only_after_stop_preserves_run_and_does_not_duplicate(self):
        payload = {"title": "frame", "kind": "image", "skill_ids": [self.edit["id"]]}
        result = self.tasks.create("topic", request_id="archive-test", tasks=[payload])
        task = result["items"][0]
        with self.assertRaises(ContentRunError):
            self.tasks.control("topic", task["id"], "remove")
        run = self.runs.get(task["run_id"])
        self.runs.cancel(run.id, expected_version=run.version)
        removed = self.tasks.control("topic", task["id"], "remove")
        self.assertEqual(removed["items"], [])
        self.assertEqual(self.runs.get(run.id).status.value, "cancelled")
        with self.db.session() as session:
            stored = session.get(ProductionTask, task["id"])
            self.assertIsNotNone(stored.archived_at)
            self.assertIsNotNone(session.get(ContentRun, run.id))
        replay = self.tasks.create("topic", request_id="archive-test", tasks=[payload])
        self.assertEqual(replay["items"][0]["id"], task["id"])
        self.assertEqual(self.tasks.list("topic")["items"], [])

    def test_remove_http_enforces_account_boundary(self):
        from fastapi import FastAPI, Request
        from fastapi.testclient import TestClient
        from creatoros.storage import ContentRepository, CreatorPlatform
        from creatoros.web.agent_scope import AgentScopeGuard
        from creatoros.web.production_task_routes import production_task_routes

        result = self.tasks.create("topic", request_id="scope-test", tasks=[{
            "title": "frame", "kind": "image", "skill_ids": [self.edit["id"]]}])
        task = result["items"][0]
        run = self.runs.get(task["run_id"])
        self.runs.cancel(run.id, expected_version=run.version)
        ContentRepository(self.db).create_creator(creator_id="other", display_name="Other", platform=CreatorPlatform.XIAOHONGSHU)

        class Sessions:
            def get(self, session_id):
                return {"scope_kind": "creator", "creator_id": session_id}

        guard = AgentScopeGuard(self.db, Sessions(), None)
        app = FastAPI()

        @app.middleware("http")
        async def scope(request: Request, call_next):
            return await guard.check(request) or await call_next(request)

        app.include_router(production_task_routes(self.tasks))
        path = f"/api/topics/topic/production-tasks/{task['id']}/remove"
        with TestClient(app) as client:
            denied = client.post(path, headers={"x-creatoros-agent-session": "other"})
            self.assertEqual(denied.status_code, 403)
            self.assertEqual(len(self.tasks.list("topic")["items"]), 1)
            allowed = client.post(path, headers={"x-creatoros-agent-session": "creator"})
            self.assertEqual(allowed.status_code, 200, allowed.text)
            self.assertEqual(self.tasks.list("topic")["items"], [])
