"""Read-only task indexes over real isolated SQLite/HTTP; no media/runtime needed."""
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
import json

import httpx

from creatoros.storage import ContentRun, Creator, CreatorPlatform, MediaCollection, ProductionTask, Series, TopicRemoval
from creatoros.tools.model_projection import project_model_data
from creatoros.web.observation import ObservationService, encode
from creatoros.web.observation_routes import observation_routes
from creatoros.web.worker_tasks import worker_task_routes
from creatoros.web.chat_links import tool_links
from tests.agent_studio_support import serve
from tests import test_work_inbox as inbox_fixture


class StagedTaskVisibilityTests(unittest.TestCase):
    def setUp(self):
        inbox_fixture.WorkInboxTests.setUp(self)
        self.research.list = lambda _: []
        self.discussions = SimpleNamespace(root=self.root / "discussions", list_for_creator=lambda *args, **kwargs: [])
        self.app.include_router(worker_task_routes(self.db, self.research, self.discussions))
        self.observation = ObservationService(self.db, chat=SimpleNamespace(root=self.root / "chat"),
            research=self.research, discussions=self.discussions, extractions=self.extractions,
            runs=SimpleNamespace(output_root=self.root / "outputs"))
        self.app.include_router(observation_routes(self.observation))
        with self.db.session() as session:
            session.add(MediaCollection(id="collection", creator_id="account", name="镜头素材集", request_id="test",
                create_digest="a" * 64, topic_id="topic"))
        with self.db.session() as session:
            for position, status in enumerate(("waiting", "needs_attention", "cancelled")):
                session.add(ProductionTask(id=status, topic_id="topic", title="剪辑：" + status, position=position,
                    collection_id="collection", create_request_id=status, kind="video", dispatch_state=status,
                    definition_json={"creator_id": "account", "series_id": "series", "PRIVATE_DEFINITION": {"path": "D:/private"}},
                    dependency_json=[], created_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
                    updated_at=datetime(2000, 1, 1, tzinfo=timezone.utc)))

    tearDown = inbox_fixture.WorkInboxTests.tearDown
    hashes = inbox_fixture.WorkInboxTests.hashes

    def test_wait_attention_cancelled_are_visible_with_named_links_and_no_definitions(self):
        before = self.hashes()
        with serve(self.app) as base, httpx.Client(base_url=base) as client:
            response = client.get("/api/creators/account/tasks")
            self.assertEqual(response.status_code, 200, response.text)
            tasks = response.json()
            pending = [item for item in tasks["items"] if item["kind"] == "production_task"]
            self.assertEqual({item["status"] for item in pending}, {"waiting", "needs_attention", "cancelled"})
            self.assertEqual(tasks["items"][0]["id"], "waiting")  # Even an old wait remains findable.
            self.assertEqual(tasks["summary"]["active"], 1)
            self.assertEqual(tasks["summary"]["failed"], 0)
            self.assertEqual(tasks["summary"]["needs_attention"], 1)
            filtered = client.get("/api/creators/account/tasks?statuses=waiting").json()
            self.assertEqual([item["id"] for item in filtered["items"]], ["waiting"])
            model = project_model_data("get_creator_tasks", tasks)
            self.assertEqual(model["items"][0]["topic_title"], "完成的作品")
            self.assertEqual(model["items"][0]["collection_name"], "镜头素材集")
            self.assertNotIn("PRIVATE_DEFINITION", str(model))
            links = tool_links("get_creator_tasks", json.dumps(tasks))
            self.assertTrue(any(link["label"] == "选题 · 完成的作品" for link in links))
            self.assertTrue(any(link["label"] == "素材 · 镜头素材集" for link in links))
            inbox = client.get("/api/work-inbox").json()
            self.assertEqual({item["bucket"] for item in inbox["items"] if item["kind"] == "production_task"}, {"active", "attention", "recent"})
            node = encode("production_task", "waiting")
            detail = client.get("/api/observation/detail", params={"node_id": node}).json()
            self.assertEqual(detail["label"], "剪辑：waiting")
            self.assertEqual(detail["status"], "waiting")
            self.assertTrue(any(link["label"] == "素材集：镜头素材集" for link in detail["links"]))
            self.assertNotIn("PRIVATE_DEFINITION", str(detail))
            self.assertNotIn("D:/private", str(detail))
            children = client.get("/api/observation/tree", params={"parent": encode("topic", "topic")}).json()["items"]
            self.assertEqual(sum(item["kind"] == "production_task" for item in children), 3)
            self.assertEqual(client.get("/api/creators/account/tasks").json()["items"], tasks["items"])
        self.assertEqual(self.hashes(), before)

    def test_dispatched_run_is_counted_once_even_if_task_state_is_stale(self):
        with self.db.session() as session:
            session.get(ContentRun, "run").task_id = "waiting"
        with serve(self.app) as base, httpx.Client(base_url=base) as client:
            tasks = client.get("/api/creators/account/tasks").json()["items"]
            self.assertFalse(any(item["id"] == "waiting" for item in tasks))
            self.assertEqual(sum(item["kind"] == "production" for item in tasks), 1)
            inbox = client.get("/api/work-inbox").json()["items"]
            self.assertFalse(any(item["id"] == "waiting" for item in inbox))
            children = client.get("/api/observation/tree", params={"parent": encode("topic", "topic")}).json()["items"]
            self.assertEqual(sum(item["kind"] == "run" for item in children), 1)
            self.assertNotIn(encode("production_task", "waiting"), [item["id"] for item in children])

    def test_foreign_frozen_tasks_removed_topics_and_moved_series_are_excluded(self):
        with self.db.session() as session:
            session.get(ProductionTask, "waiting").definition_json = {"creator_id": "other", "series_id": "series"}
        with serve(self.app) as base, httpx.Client(base_url=base) as client:
            for path in ("/api/creators/account/tasks", "/api/work-inbox"):
                self.assertNotIn("waiting", [item["id"] for item in client.get(path).json()["items"]])
            detail = client.get("/api/observation/detail", params={"node_id": encode("production_task", "waiting")})
            self.assertEqual(detail.status_code, 404)
            with self.db.session() as session:
                session.add(TopicRemoval(topic_id="topic", series_id="series", creator_id="account", request_id="removed", status="archived"))
            self.assertEqual(client.get("/api/creators/account/tasks").json()["items"], [])
            self.assertFalse(any(item["kind"] == "production_task" for item in client.get("/api/work-inbox").json()["items"]))
            with self.db.session() as session:
                session.add(Creator(id="other", display_name="其他账号", platform=CreatorPlatform.XIAOHONGSHU))
            with self.db.session() as session:
                session.get(Series, "series").creator_id = "other"
            self.assertFalse(any(item["kind"] == "production_task" for item in client.get("/api/work-inbox").json()["items"]))


if __name__ == "__main__":
    unittest.main()
