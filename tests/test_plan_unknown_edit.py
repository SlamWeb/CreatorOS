"""Unknown continuation results remain frozen when a plan is edited."""
from concurrent.futures import Future
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from creatoros.agent.plans import PlanService
from creatoros.storage import Base, ContentRepository, CreatorPlatform, Database
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
    """Record dispatch and run only the patched local tool, never a producer."""
    def __init__(self):
        self.calls = []

    def submit(self, fn, *args):
        self.calls.append(args)
        future = Future()
        try:
            future.set_result(fn(*args))
        except Exception as error:
            future.set_exception(error)
            raise
        return future


class UnknownContinuationEditTests(unittest.TestCase):
    def check_edit_and_resume(self, *, resume_before_crash):
        with TemporaryDirectory(prefix="plan-unknown-edit-") as directory, ExitStack() as cleanup:
            root = Path(directory)
            db = Database("sqlite:///" + (root / "test.db").as_posix())
            cleanup.callback(db.close)
            Base.metadata.create_all(db.engine)
            ContentRepository(db).create_creator(creator_id="isolated", display_name="isolated",
                                                platform=CreatorPlatform.XIAOHONGSHU)
            plans = PlanService(db, IsolatedChat(root))
            plans._pool = InlinePool()
            session_id = "isolated-session"
            steps = [
                {"id": "review", "title": "review", "tool": "request_review",
                 "arguments": {"question": "check"}, "depends_on": [], "wait_for_completion": False},
                {"id": "image", "title": "image", "tool": "start_content_run",
                 "arguments": {"action": "tasks", "topic_id": "isolated-topic", "tasks": [
                     {"kind": "image", "title": "image", "brief": "draw"}]},
                 "depends_on": ["review"], "wait_for_completion": False},
            ]
            plan = plans.create(session_id, "request", "plan", steps, "http://127.0.0.1:1")
            plan_id = plan["id"]
            plans.advance()
            event = plans.claim_continuation(session_id, "worker")
            self.assertTrue(plans.begin_continuation_output(plan_id, event["event_id"], "worker"))
            if resume_before_crash:
                detail = plans.get(session_id, plan_id)
                plans.action(session_id, plan_id, detail["version"], "resume")
            self.assertTrue(plans.fail_continuation(plan_id, event["event_id"], "worker", outcome="unknown"))

            with patch("creatoros.agent.plans.execute_tool_call", return_value=ToolResult(content='{"run_id":"isolated"}')) as execute:
                # Both an unchanged submission and an actual edit preserve the freeze.
                detail = plans.get(session_id, plan_id)
                unchanged = plans.edit(session_id, plan_id, detail["version"], steps)
                edited_steps = deepcopy(steps)
                edited_steps[1]["title"] = "edited image"
                edited = plans.edit(session_id, plan_id, unchanged["version"], edited_steps)
                self.assertEqual(edited["steps"][1]["title"], "edited image")
                self.assertEqual(edited["continuation_state"], "unknown")
                for _ in range(3):
                    plans.advance()
                frozen = plans.get(session_id, plan_id)
                self.assertEqual(frozen["continuation_state"], "unknown")
                self.assertEqual(frozen["steps"][1]["status"], "pending")
                self.assertEqual(len(plans._pool.calls), 0)
                self.assertEqual(execute.call_count, 0)

                # A running plan requires an explicit pause/resume; an already
                # paused plan can be explicitly resumed directly.
                if frozen["status"] == "running":
                    frozen = plans.action(session_id, plan_id, frozen["version"], "pause")
                resumed = plans.action(session_id, plan_id, frozen["version"], "resume")
                self.assertEqual(resumed["continuation_state"], "none")
                for _ in range(3):
                    plans.advance()
                completed = plans.get(session_id, plan_id)
                self.assertEqual(completed["steps"][1]["status"], "succeeded")
                self.assertEqual(len(plans._pool.calls), 1)
                self.assertEqual(execute.call_count, 1)
                self.assertEqual(plans._pool.calls[0][-2], "start_content_run")

    def test_edit_after_unknown_with_running_plan(self):
        self.check_edit_and_resume(resume_before_crash=True)

    def test_edit_after_unknown_with_paused_plan(self):
        self.check_edit_and_resume(resume_before_crash=False)


if __name__ == "__main__":
    unittest.main()
