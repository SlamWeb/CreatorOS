"""Repeated pending reads retain their receipts without repeated long bodies."""
import json
import unittest
from copy import deepcopy

from creatoros.tools.model_projection import project_tool_messages


def step(call_id, result, job="job-1"):
    return [
        {"role": "assistant", "tool_calls": [{"id": call_id, "name": "get_skill_extraction",
                                               "arguments": json.dumps({"job_id": job})}]},
        {"role": "tool", "tool_call_id": call_id, "content": json.dumps(result, ensure_ascii=False)},
    ]


def pending():
    return {"id": "job-1", "status": "running", "operation": "extract", "error": None,
            "note": "正在等待已受理的后台操作。" * 120}


class PendingReceiptDedupTest(unittest.TestCase):
    def test_exact_repeat_ref_keeps_current_state_and_raw_ledger(self):
        history = step("first", pending()) + step("second", pending())
        original = deepcopy(history)
        view = project_tool_messages(history)
        self.assertEqual(history, original)
        second = json.loads(view[-1]["content"])
        self.assertEqual(second["id"], "job-1")
        self.assertEqual(second["status"], "running")
        self.assertEqual(second["result_ref"]["tool_call_id"], "first")
        self.assertTrue(second["result_ref"]["identical"])
        self.assertLess(len(view[-1]["content"]), len(view[1]["content"]) // 2)
        self.assertEqual(project_tool_messages(history[:2]), view[:2])

    def test_changed_arguments_status_content_or_error_does_not_reference(self):
        for second, job in [({**pending(), "note": "变化" * 600}, "job-1"),
                            ({**pending(), "status": "completed"}, "job-1"),
                            ({**pending(), "error": "failure"}, "job-1"),
                            ({**pending(), "outcome_unknown": True}, "job-1"),
                            ({**pending(), "queue_state": "unknown"}, "job-1"),
                            ({**pending(), "observation_warning": "读取状态不确定"}, "job-1"),
                            (pending(), "job-2")]:
            with self.subTest(second=second.get("status"), job=job):
                view = project_tool_messages(step("first", pending()) + step("second", second, job))
                self.assertNotIn("result_ref", json.loads(view[-1]["content"]))

    def test_raw_change_hidden_by_projection_never_matches(self):
        old = {**pending(), "internal_version": 1}
        new = {**pending(), "internal_version": 2}
        view = project_tool_messages(step("first", old) + step("second", new))
        self.assertNotIn("result_ref", json.loads(view[-1]["content"]))

    def test_identical_uncertain_receipts_are_never_referenced(self):
        for receipt in ({**pending(), "outcome_unknown": True},
                        {**pending(), "queue_state": "unknown"},
                        {**pending(), "observation_warning": "读取状态不确定"}):
            with self.subTest(receipt=receipt):
                view = project_tool_messages(step("first", receipt) + step("second", receipt))
                self.assertNotIn("result_ref", json.loads(view[-1]["content"]))

    def test_no_reference_without_unambiguous_visible_source(self):
        self.assertNotIn("result_ref", json.loads(project_tool_messages(step("second", pending()))[-1]["content"]))
        repeated_id = step("same", pending()) + step("same", pending())
        self.assertNotIn("result_ref", json.loads(project_tool_messages(repeated_id)[-1]["content"]))
        externalized = step("first", pending()) + step("second", pending())
        externalized[1]["content"] = "[完整工具结果已外置，请按本会话索引读取]"
        self.assertNotIn("result_ref", json.loads(project_tool_messages(externalized)[-1]["content"]))


if __name__ == "__main__":
    unittest.main()
