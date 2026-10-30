"""Current-revision previews of single tasks, using isolated SQLite and local images."""
from datetime import datetime, timezone
import unittest
from creatoros.storage import ProductionTask
from tests import test_topic_library_production as fixtures


class SingleTaskPreviewTests(unittest.TestCase):
    setUp = fixtures.TopicProductionViewTests.setUp
    library = fixtures.TopicProductionViewTests.library
    def child(self):
        with self.db.session() as session:
            session.add(ProductionTask(id="child", topic_id="review-1", title="One image",
                position=0, create_request_id="preview-child"))
        run = self.runs._create("review-1", task_id="child")
        self.producer.count = 1
        self.runs.execute(run.id)
        return run.id

    def row(self):
        return next(item for item in self.library()["items"] if item["id"] == "review-1")

    def test_single_child_preview_is_read_only_and_preserves_action_contract(self):
        identity = self.child()
        row = self.row()
        self.assertIsNone(row["existing_run_id"])
        self.assertEqual(row["preview_run_id"], identity)
        self.assertEqual(row["preview_run_status"], "awaiting_approval")
        self.assertEqual(row["production_task_count"], 1)
        self.assertEqual(row["production_state"], "produced")
        self.assertEqual(row["card_count"], 1)
        self.assertEqual(self.client.get(row["cover_url"]).status_code, 200)
        self.row()
        self.assertEqual(self.producer.calls, 1)

    def test_waiting_second_task_prevents_single_task_projection(self):
        self.child()
        with self.db.session() as session:
            session.add(ProductionTask(id="waiting", topic_id="review-1", title="Video later",
                position=1, create_request_id="preview-wait", dispatch_state="waiting", kind="video"))
        row = self.row()
        self.assertEqual(row["production_task_count"], 2)
        self.assertIsNone(row["preview_run_id"])
        self.assertIsNone(row["cover_url"])
        with self.db.session() as session:
            session.get(ProductionTask, "waiting").archived_at = datetime.now(timezone.utc)
        self.assertEqual(self.row()["production_task_count"], 1)
        self.assertIsNotNone(self.row()["cover_url"])

    def test_current_rework_never_displays_previous_revision_image(self):
        identity = self.child()
        old = self.runs.get_active_revision(identity)
        current = self.runs.get(identity)
        response = self.client.post(f"/api/runs/{identity}/revisions", json={
            "instruction": "isolated edit", "expected_version": current.version})
        self.assertEqual(response.status_code, 201, response.text)
        row = self.row()
        self.assertEqual(row["preview_run_id"], identity)
        self.assertIsNone(row["cover_url"])
        self.assertEqual(row["production_state"], "unproduced")
        from pathlib import Path
        self.assertTrue((Path(old.artifact_directory) / "images/01.png").is_file())
        self.assertEqual(self.producer.calls, 1)

    def test_waiting_only_task_has_no_direct_run(self):
        with self.db.session() as session:
            session.add(ProductionTask(id="waiting", topic_id="review-1", title="Later",
                position=0, create_request_id="wait-only", dispatch_state="waiting"))
        row = self.row()
        self.assertEqual(row["production_task_count"], 1)
        self.assertIsNone(row["preview_run_id"])
        self.assertEqual(row["available_actions"], ["view"])
