"""Plan argument reuse is a model-view optimization, never a business write."""
from copy import deepcopy
import json
import unittest

from creatoros.tools.model_projection import project_tool_messages


def call(call_id, plan, *, action="get"):
    return [
        {"role": "assistant", "tool_calls": [{"id": call_id, "name": "manage_plan",
         "arguments": json.dumps({"action": action})}]},
        {"role": "tool", "tool_call_id": call_id, "content": json.dumps(plan, ensure_ascii=False)},
    ]


def plan(*, version=1, step_id="step-a", body="资料" * 600, status="pending"):
    return {"id": "plan-a", "version": version, "status": status, "steps": [
        {"id": step_id, "tool": "request_review", "arguments": {"question": body},
         "status": status, "depends_on": ["prior"], "error": "check" if status == "unknown" else None,
         "retryable": False, "result": {"status": status, "id": "object-a"}},
        {"id": "step-b", "tool": "request_review", "arguments": {"question": body + "b"},
         "status": "pending", "depends_on": []},
    ]}


class PlanArgumentContextTest(unittest.TestCase):
    def test_identical_arguments_reference_visible_first_and_keep_independent_status(self):
        messages = call("call-first", plan()) + call("call-next", plan(version=2, status="unknown"))
        before = deepcopy(messages)
        projected = project_tool_messages(messages)
        first, second = json.loads(projected[1]["content"]), json.loads(projected[3]["content"])
        self.assertEqual(first["steps"][0]["arguments"], plan()["steps"][0]["arguments"])
        for index in (0, 1):
            step = second["steps"][index]
            self.assertNotIn("arguments", step)
            self.assertEqual(step["arguments_ref"], {"tool_call_id": "call-first", "step_id": step["id"],
                                                     "field": "arguments", "same_value": True})
        self.assertEqual(second["version"], 2)
        self.assertEqual(second["status"], "unknown")
        self.assertEqual(second["steps"][0]["depends_on"], ["prior"])
        self.assertEqual(second["steps"][0]["error"], "check")
        self.assertEqual(second["steps"][0]["result"]["status"], "unknown")
        self.assertFalse(second["steps"][0]["retryable"])
        self.assertEqual(messages, before)
        self.assertEqual(project_tool_messages(projected), projected)

    def test_removed_reference_source_restores_arguments(self):
        full = call("call-first", plan()) + call("call-next", plan(version=2))
        retained = full[2:]
        projected = json.loads(project_tool_messages(retained)[1]["content"])
        self.assertEqual(projected["steps"][0]["arguments"], plan()["steps"][0]["arguments"])
        self.assertNotIn("arguments_ref", projected["steps"][0])

    def test_changed_plan_step_tool_or_body_is_not_deduplicated(self):
        first = plan()
        variants = [plan(body="不一样" * 600), plan(step_id="different")]
        different_plan = plan()
        different_plan["id"] = "plan-b"
        variants.append(different_plan)
        different_tool = plan()
        different_tool["steps"][0]["tool"] = "manage_topics"
        variants.append(different_tool)
        for variant in variants:
            with self.subTest(variant=variant["steps"][0]["tool"]):
                projected = project_tool_messages(call("first", first) + call("next", variant))
                self.assertIn("arguments", json.loads(projected[-1]["content"])["steps"][0])

    def test_reused_call_or_step_ids_do_not_create_ambiguous_references(self):
        reused = call("same", plan()) + call("same", plan(version=2))
        for index in (1, 3):
            self.assertIn("arguments", json.loads(project_tool_messages(reused)[index]["content"])["steps"][0])
        duplicate_steps = plan()
        duplicate_steps["steps"][1]["id"] = "step-a"
        messages = call("first", plan()) + call("next", duplicate_steps)
        projected = json.loads(project_tool_messages(messages)[-1]["content"])
        self.assertTrue(all("arguments" in step for step in projected["steps"]))

    def test_write_receipt_and_small_arguments_preserve_existing_contract(self):
        created = json.loads(project_tool_messages(call("write", plan(), action="create"))[-1]["content"])
        self.assertNotIn("arguments", created["steps"][0])
        self.assertNotIn("arguments_ref", created["steps"][0])
        small = plan(body="short")
        result = json.loads(project_tool_messages(call("first", small) + call("second", small))[-1]["content"])
        self.assertEqual(result["steps"][0]["arguments"], {"question": "short"})


if __name__ == "__main__":
    unittest.main()
