"""Plan edits cannot dispatch work while a continuation remains unresolved."""
from concurrent.futures import Future
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from creatoros.agent.plans import PlanService
from creatoros.storage import Base, ContentRepository, CreatorPlatform, Database
from creatoros.storage.models import AgentPlan
from creatoros.tools.results import ToolResult


class IsolatedChat:
    def __init__(self, root):
        self.root = root

    def get(self, session_id):
        return {"creator_id": "isolated", "scope_kind": "creator"}

    def require_creator(self, creator_id):
        assert creator_id == "isolated"

    def _path(self, session_id):
        return self.root / session_id / "view.json"


class InlinePool:
    def submit(self, fn, *args):
        future = Future()
        future.set_result(fn(*args))
        return future


class ContinuationScheduleGateTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="continuation-schedule-gate-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.db = Database("sqlite:///" + (root / "test.db").as_posix())
        self.addCleanup(self.db.close)
        Base.metadata.create_all(self.db.engine)
        ContentRepository(self.db).create_creator(creator_id="isolated", display_name="isolated",
                                                  platform=CreatorPlatform.XIAOHONGSHU)
        self.plans = PlanService(self.db, IsolatedChat(root))
        self.plans._pool = InlinePool()
        self.calls = []

        def execute(tool_call, **kwargs):
            self.calls.append(tool_call.name)
            if tool_call.name == "get_producer_skill":
                return ToolResult(content='{"message":"controlled read failure"}', is_error=True,
                                  error_type="studio_read_failed")
            return ToolResult(content='{"run_id":"isolated"}')

        tool_patch = patch("creatoros.agent.plans.execute_tool_call", side_effect=execute)
        tool_patch.start()
        self.addCleanup(tool_patch.stop)
        self.sid = "isolated-session"
        self.read = {"id": "read", "title": "read", "tool": "get_producer_skill",
                     "arguments": {"skill_id": "missing--0000000000000000"}}
        self.image = {"id": "image", "title": "image", "tool": "start_content_run",
                      "arguments": {"action": "tasks", "topic_id": "isolated-topic", "tasks": [
                          {"kind": "image", "title": "image", "brief": "draw"}]}}
        plan = self.plans.create(self.sid, "request", "plan", [self.read], "http://127.0.0.1:1")
        self.plan_id = plan["id"]
        self.plans.advance()
        self.plans.advance()
        self.assertEqual(self.detail()["status"], "needs_attention")
        self.assertEqual(self.detail()["continuation_state"], "pending")

    def detail(self):
        return self.plans.get(self.sid, self.plan_id)

    def edit_and_assert_frozen(self, state):
        before = self.detail()
        self.plans.edit(self.sid, self.plan_id, before["version"], [self.read, self.image])
        changed = deepcopy(self.image)
        changed["title"] = "edited image"
        self.plans.edit(self.sid, self.plan_id, self.detail()["version"], [self.read, changed])
        for _ in range(3):
            self.plans.advance()
        current = self.detail()
        self.assertEqual(current["status"], "running")
        self.assertEqual(current["continuation_state"], state)
        self.assertEqual(current["steps"][1]["title"], "edited image")
        self.assertEqual(current["steps"][1]["status"], "pending")
        self.assertEqual(self.calls, ["get_producer_skill"])
        return current

    def assert_dispatched_once(self):
        for _ in range(3):
            self.plans.advance()
        self.assertEqual(self.calls, ["get_producer_skill", "start_content_run"])
        self.assertEqual(self.detail()["steps"][1]["status"], "succeeded")

    def test_pending_edit_waits_for_ack_then_dispatches_once(self):
        self.edit_and_assert_frozen("pending")
        event = self.plans.claim_continuation(self.sid, "worker")
        self.assertEqual((event["status"], event["reason"]), ("running", "plan_updated"))
        self.assertEqual([step["status"] for step in event["steps"]], ["failed", "pending"])
        self.assertTrue(self.plans.begin_continuation_output(self.plan_id, event["event_id"], "worker"))
        self.assertTrue(self.plans.ack_continuation(self.plan_id, event["event_id"], "worker"))
        self.assert_dispatched_once()
        next_event = self.plans.claim_continuation(self.sid, "next-worker")
        self.assertNotEqual(next_event["event_id"], event["event_id"])
        self.assertFalse(self.plans.ack_continuation(self.plan_id, event["event_id"], "worker"))
        with self.db.session() as session:
            plan = session.get(AgentPlan, self.plan_id)
            self.assertEqual((plan.continuation_state, plan.continuation_token), ("claimed", "next-worker"))

    def test_claimed_or_executing_edit_and_explicit_resume_do_not_steal_worker(self):
        event = self.plans.claim_continuation(self.sid, "worker")
        current = self.edit_and_assert_frozen("claimed")
        paused = self.plans.action(self.sid, self.plan_id, current["version"], "pause")
        self.plans.action(self.sid, self.plan_id, paused["version"], "resume")
        with self.db.session() as session:
            plan = session.get(AgentPlan, self.plan_id)
            self.assertEqual((plan.continuation_state, plan.continuation_token), ("claimed", "worker"))
        self.plans.advance()
        self.assertEqual(self.calls, ["get_producer_skill"])
        self.assertTrue(self.plans.begin_continuation_output(self.plan_id, event["event_id"], "worker"))
        current = self.detail()
        self.plans.edit(self.sid, self.plan_id, current["version"], [self.read, self.image])
        current = self.detail()
        paused = self.plans.action(self.sid, self.plan_id, current["version"], "pause")
        self.plans.action(self.sid, self.plan_id, paused["version"], "resume")
        self.plans.advance()
        with self.db.session() as session:
            plan = session.get(AgentPlan, self.plan_id)
            self.assertEqual((plan.continuation_state, plan.continuation_token), ("executing", "worker"))
        self.assertEqual(self.calls, ["get_producer_skill"])
        self.assertTrue(self.plans.ack_continuation(self.plan_id, event["event_id"], "worker"))
        self.assert_dispatched_once()

    def test_explicit_pause_resume_can_override_unclaimed_pending_event(self):
        current = self.edit_and_assert_frozen("pending")
        paused = self.plans.action(self.sid, self.plan_id, current["version"], "pause")
        resumed = self.plans.action(self.sid, self.plan_id, paused["version"], "resume")
        self.assertEqual(resumed["continuation_state"], "none")
        self.assert_dispatched_once()

    def test_safe_retry_does_not_steal_a_claimed_continuation(self):
        event = self.plans.claim_continuation(self.sid, "worker")
        retried = self.plans.action(self.sid, self.plan_id, self.detail()["version"], "retry", "read")
        self.assertEqual(retried["steps"][0]["status"], "pending")
        with self.db.session() as session:
            plan = session.get(AgentPlan, self.plan_id)
            self.assertEqual((plan.continuation_state, plan.continuation_token), ("claimed", "worker"))
        self.plans.advance()
        self.assertEqual(self.calls, ["get_producer_skill"])
        self.assertTrue(self.plans.begin_continuation_output(self.plan_id, event["event_id"], "worker"))
        self.assertTrue(self.plans.ack_continuation(self.plan_id, event["event_id"], "worker"))
        self.plans.advance()
        self.assertEqual(self.calls, ["get_producer_skill", "get_producer_skill"])

    def test_safe_retry_does_not_steal_an_executing_continuation(self):
        event = self.plans.claim_continuation(self.sid, "worker")
        self.assertTrue(self.plans.begin_continuation_output(self.plan_id, event["event_id"], "worker"))
        self.plans.action(self.sid, self.plan_id, self.detail()["version"], "retry", "read")
        with self.db.session() as session:
            plan = session.get(AgentPlan, self.plan_id)
            self.assertEqual((plan.continuation_state, plan.continuation_token), ("executing", "worker"))
        self.plans.advance()
        self.assertEqual(self.calls, ["get_producer_skill"])
        self.assertTrue(self.plans.ack_continuation(self.plan_id, event["event_id"], "worker"))
        self.plans.advance()
        self.assertEqual(self.calls, ["get_producer_skill", "get_producer_skill"])


if __name__ == "__main__":
    unittest.main()
