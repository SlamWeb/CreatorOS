"""Real SQLite/HTTP with local image fixtures, not a Codex quality evaluation."""
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile
from io import BytesIO

from fastapi.testclient import TestClient
from creatoros.storage import ContentRun, Topic
from creatoros.runs import ContentRunError
from tests.studio_review_fixtures import make_fixture


class ProductionTaskCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db, self.runs, self.producer, self.app = make_fixture(Path(self.temp.name))
        self.addCleanup(self.db.close)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.producer.count = 1
        self.old = self.runs.create("review-1")
        self.runs.execute(self.old.id)
        with self.db.session() as session:
            self.before = self.record(session.get(ContentRun, self.old.id))
            self.topic_before = self.record(session.get(Topic, "review-1"))
        self.original_files = {str(file): file.read_bytes() for file in self.runs.output_root.rglob("*") if file.is_file()}

    @staticmethod
    def record(row):
        return {column.name: getattr(row, column.name) for column in row.__table__.columns}

    def wait(self, ids):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            rows = [self.runs.get(identity) for identity in ids]
            if all(row.status.value not in {"queued", "producing", "validating"} for row in rows):
                self.assertEqual([row.status.value for row in rows], ["awaiting_approval"] * len(rows))
                return
            time.sleep(.05)
        self.fail("Isolated fixture production did not finish")

    def test_current_child_outputs_order_zip_and_old_records_unchanged(self):
        endpoint = "/api/topics/review-1/production-tasks"
        body = dict(request_id="collection-tasks", tasks=[
            dict(title="第一幕", brief="清晨，一张图片。"), dict(title="第二幕", brief="夜晚，一张图片。")])
        response = self.client.post(endpoint, json=body)
        self.assertEqual(response.status_code, 202, response.text)
        result = response.json()
        ids = [task["run_id"] for task in result["items"]]
        self.wait(ids)
        collection_url = f"/api/creators/review-lab/collections/{result['collection_id']}"
        collection = self.client.get(collection_url).json()
        self.assertEqual([item["run_id"] for item in collection["items"]], ids)
        self.assertNotIn(self.old.id, [item["run_id"] for item in collection["sources"]])
        for identity in ids:
            detail = self.client.get(f"/api/runs/{identity}").json()
            self.assertEqual(detail["latest_run_id"], identity)
            self.assertEqual(detail["production_collection_id"], result["collection_id"])
            self.assertIn("approve", detail["allowed_actions"])
        topic = self.client.get("/api/topics/review-1").json()
        self.assertEqual(topic["existing_run_id"], self.old.id)
        self.assertEqual(topic["production_task_count"], 2)
        self.assertEqual(topic["production_collection_id"], result["collection_id"])
        self.assertEqual(topic["production_task_active_count"], 0)
        before_calls = self.producer.calls
        replay = self.client.post(endpoint, json=body).json()
        self.assertEqual([task["run_id"] for task in replay["items"]], ids)
        self.assertEqual(self.producer.calls, before_calls)
        archive = self.client.get(collection_url + "/download", params={
            "version": collection["version"], "snapshot": collection["snapshot_digest"]})
        self.assertEqual(archive.status_code, 200, archive.text if archive.status_code != 200 else "")
        with ZipFile(BytesIO(archive.content)) as zipped:
            self.assertEqual(len([name for name in zipped.namelist() if name.endswith(".png")]), 2)
        first = self.runs.get(ids[0])
        fresh = self.runs.restart_fresh(first.id, expected_version=first.version, request_id="task-fresh")
        self.runs.execute(fresh.id)
        collection = self.client.get(collection_url).json()
        self.assertEqual([item["run_id"] for item in collection["items"]], [fresh.id, ids[1]])
        self.assertEqual(self.client.get(f"/api/runs/{ids[1]}").json()["latest_run_id"], ids[1])
        self.assertEqual(self.client.get(f"/api/runs/{ids[0]}").json()["latest_run_id"], fresh.id)
        with self.db.session() as session:
            self.assertEqual(self.record(session.get(ContentRun, self.old.id)), self.before)
            self.assertEqual(self.record(session.get(Topic, "review-1")), self.topic_before)
        for path, data in self.original_files.items():
            self.assertEqual(Path(path).read_bytes(), data)
        deleted = self.client.delete(collection_url, params={"expected_version": collection["version"]},
            headers={"content-type": "application/json"})
        self.assertEqual(deleted.status_code, 409, deleted.text)

    def test_task_only_topic_cannot_accidentally_start_another_whole_topic_run(self):
        response = self.client.post("/api/topics/review-2/production-tasks", json={
            "request_id": "task-only", "tasks": [{"title": "第一部分", "brief": "只制作一张。"}]})
        self.assertEqual(response.status_code, 202, response.text)
        self.wait([response.json()["items"][0]["run_id"]])
        with self.assertRaises(ContentRunError) as caught:
            self.runs.create("review-2")
        self.assertEqual(caught.exception.code, "production_tasks_exist")
        self.assertNotIn("start", self.client.get("/api/topics/review-2").json()["available_actions"])


if __name__ == "__main__":
    unittest.main()
