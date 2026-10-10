"""Real isolated HTTP/SQLite checks; no model or generated-image scores."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import httpx
from pydantic import ValidationError

from creatoros.context import RuntimeContext
from creatoros.evaluation.fixture import E01Fixture
from creatoros.tools.content_discussion import CreatorTasksArgs, get_creator_tasks
from creatoros.tools.model_projection import project_model_data
from creatoros.web.chat_links import tool_links
from tests.agent_studio_support import serve


class TaskNavigationTests(unittest.TestCase):
    def test_requested_statuses_filter_real_records_receipts_and_counts(self):
        with TemporaryDirectory() as temporary:
            world = E01Fixture(Path(temporary) / "fixture", None, case_id="E10")
            try:
                with serve(world.app) as base:
                    session = world.app.state.chat.create(world.creator_a)
                    context = RuntimeContext(project_root=world.root, studio_url=base,
                        creator_id=world.creator_a, agent_session_id=session["id"])
                    before = world.state()
                    result = get_creator_tasks(world.creator_a, world.e10["series_id"],
                                               statuses=["failed"], context=context)
                    self.assertFalse(result.is_error, result.content)
                    data = json.loads(result.content)
                    self.assertEqual({row["id"] for row in data["items"]},
                                     {row["id"] for row in world.e10["failed_tasks"]})
                    self.assertTrue(all(row["status"] == "failed" for row in data["items"]))
                    self.assertEqual(data["summary"], {"active": 0, "awaiting_approval": 0, "failed": 2})
                    self.assertEqual(data["filter"], {"series_id": world.e10["series_id"], "statuses": ["failed"]})
                    projected = project_model_data("get_creator_tasks", data)
                    self.assertEqual(projected["filter"], data["filter"])
                    self.assertEqual({row["url"] for row in tool_links("get_creator_tasks", result.content,
                                                                       studio_url=base)},
                                     {row["url"] for row in world.e10["failed_tasks"]})
                    all_rows = json.loads(get_creator_tasks(world.creator_a, world.e10["series_id"],
                                                            context=context).content)["items"]
                    self.assertTrue(set(world.e10["nonfailed_task_ids"]) <= {row["id"] for row in all_rows})
                    empty = json.loads(get_creator_tasks(world.creator_a, world.e10["series_id"],
                                                          statuses=["approved"], context=context).content)
                    self.assertEqual(empty["items"], [])
                    self.assertEqual(empty["summary"]["failed"], 0)
                    denied = get_creator_tasks(world.creator_b, statuses=["failed"], context=context)
                    self.assertTrue(denied.is_error)
                    self.assertEqual(tool_links("get_creator_tasks", denied.content, is_error=True,
                                               error_type=denied.error_type, studio_url=base), [])
                    with httpx.Client(base_url=base) as client:
                        self.assertEqual(client.get(f"/api/creators/{world.creator_a}/tasks",
                                                    params={"statuses": "not-a-status"}).status_code, 422)
                    self.assertEqual(world.state(), before)
            finally:
                world.database.close()

    def test_tool_schema_rejects_empty_unknown_or_scalar_filter(self):
        for statuses in ([], ["not-a-status"], "failed", [True]):
            with self.subTest(statuses=statuses), self.assertRaises(ValidationError):
                CreatorTasksArgs(creator_id="creator-a", statuses=statuses)
        self.assertEqual(CreatorTasksArgs(creator_id="creator-a").statuses, None)
        self.assertEqual(CreatorTasksArgs(creator_id="creator-a", statuses=["failed", "unknown"]).statuses,
                         ["failed", "unknown"])


if __name__ == "__main__":
    unittest.main()
