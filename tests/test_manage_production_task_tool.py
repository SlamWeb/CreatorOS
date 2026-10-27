"""Real loopback API and isolated SQLite; no model or image generation."""
from dataclasses import replace
import json
import unittest

from creatoros.context import RuntimeContext
from creatoros.tools.production_task_control import ManageProductionTaskArgs, manage_production_task
from creatoros.tools.model_projection import project_model_data
from creatoros.ai.types import ToolCall
from creatoros.tools import execute_tool_call
from creatoros.web.chat import ACCOUNT_TOOLS
from creatoros.runs.production_tasks import ProductionTaskService
from tests.test_staged_tasks import HeldDispatcher
from tests import test_skill_version_scope as fixtures


class ManageProductionTaskToolTests(unittest.TestCase):
    setUp = fixtures.SkillVersionScopeTests.setUp
    skill = fixtures.SkillVersionScopeTests.skill

    def test_http_stop_remove_and_account_boundary(self):
        context = RuntimeContext(self.root, studio_url=self.url, creator_id="review-lab",
                                 agent_session_id=self.account, allowed_tools=ACCOUNT_TOOLS)
        other = replace(context, creator_id="other", agent_session_id=self.other)
        self.app.state.executor.shutdown()
        held = ProductionTaskService(self.database, self.runs, HeldDispatcher())
        created = held.create("review-1", request_id="stop-tool-test",
                              tasks=[{"title": "待制作", "brief": "测试内容"}])
        task = created["items"][0]
        denied = manage_production_task("stop", "review-1", task["id"], other)
        self.assertTrue(denied.is_error)
        self.assertEqual(denied.error_type, "agent_scope_rejected")
        listing = execute_tool_call(ToolCall("list", "manage_production_task", json.dumps({
            "action": "list", "topic_id": "review-1"})), context, model_requested=True)
        self.assertFalse(listing.is_error, listing.content)
        self.assertEqual(json.loads(listing.content)["items"][0]["status"], "queued")
        stopped = manage_production_task("stop", "review-1", task["id"], context)
        self.assertFalse(stopped.is_error, stopped.content)
        self.assertEqual(json.loads(stopped.content)["status"], "cancelled")
        removed = manage_production_task("remove", "review-1", task["id"], context)
        self.assertFalse(removed.is_error, removed.content)
        self.assertEqual(json.loads(removed.content)["status"], "removed")
        self.assertEqual(self.runs.get(task["run_id"]).status.value, "cancelled")
        self.assertEqual(json.loads(manage_production_task("list", "review-1", context=context).content)["items"], [])

    def test_schema_and_projection(self):
        with self.assertRaises(ValueError):
            ManageProductionTaskArgs(action="stop", topic_id="topic")
        result = project_model_data("manage_production_task", {"action": "list", "topic_id": "t",
            "items": [{"id": "one", "status": "queued", "brief": "不重复正文"}]})
        self.assertNotIn("brief", result["items"][0])
