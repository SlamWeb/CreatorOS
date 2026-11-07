"""A definite RSI control conflict only refreshes that task's pending read."""
import json
import unittest

from creatoros.agent.pending_tasks import PendingTaskReads
from creatoros.tools.results import ToolResult


def args(**values):
    return json.dumps(values)


def pending(identity):
    return ToolResult(json.dumps({"id": identity, "status": "running"}), details={
        "pending_task": {"kind": "skill_iteration", "id": identity, "status": "running"}})


class RsiConflictCacheTests(unittest.TestCase):
    def test_each_control_conflict_invalidates_only_target_and_recaches(self):
        first, second = "a" * 64, "b" * 64
        for action in ("pause", "resume", "continue"):
            with self.subTest(action=action):
                reads = PendingTaskReads()
                one = args(action="get", iteration_id=first)
                two = args(action="get", iteration_id=second)
                reads.observe("manage_skill_iteration", one, pending(first))
                reads.observe("manage_skill_iteration", two, pending(second))
                control = args(action=action, iteration_id=first, expected_revision=1)
                reads.observe("manage_skill_iteration", control, ToolResult(
                    "冲突，需核对。", True, "conflict", details={"http_status": 409}))
                self.assertIsNone(reads.before("manage_skill_iteration", one))
                self.assertIsNotNone(reads.before("manage_skill_iteration", two))
                reads.observe("manage_skill_iteration", one, pending(first))
                self.assertIsNotNone(reads.before("manage_skill_iteration", one))

    def test_other_errors_and_predispatch_conflict_do_not_clear(self):
        identity = "a" * 64
        get = args(action="get", iteration_id=identity)
        control = args(action="pause", iteration_id=identity, expected_revision=1)
        errors = [ToolResult("unknown", True, "studio_outcome_unknown", details={"http_status": 409}),
                  ToolResult("denied", True, "agent_scope_rejected", details={"http_status": 409}),
                  ToolResult("pre-dispatch", True, "conflict", details={"http_status": 409, "pre_dispatch": True}),
                  ToolResult("other", True, "conflict", details={"http_status": 403}),
                  ToolResult("bad", True, "invalid_arguments")]
        for error in errors:
            with self.subTest(error=error.error_type, details=error.details):
                reads = PendingTaskReads()
                reads.observe("manage_skill_iteration", get, pending(identity))
                reads.observe("manage_skill_iteration", control, error)
                self.assertIsNotNone(reads.before("manage_skill_iteration", get))
