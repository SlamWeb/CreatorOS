import unittest

from creatoros.evaluation.change_rules import plan


class ChangeRulesTests(unittest.TestCase):
    def test_tool_change_requires_safety_specialist_and_chain(self):
        out = plan(["tool"])
        self.assertEqual([x["id"] for x in out["required"]],
                         ["tool_contract", "tool_safety", "tool_calling", "affected_chain"])
        self.assertTrue(out["coverage_known"])
        self.assertIn("common_regression", [x["id"] for x in out["recommended"]])

    def test_combined_scopes_promote_required_and_do_not_duplicate(self):
        out = plan(["tool", "agent_shared", "tool"])
        required = [x["id"] for x in out["required"]]
        self.assertEqual(required.count("tool_calling"), 1)
        self.assertIn("common_regression", required)
        self.assertNotIn("common_regression", [x["id"] for x in out["recommended"]])

    def test_unknown_scope_stays_visible_and_cannot_claim_covered(self):
        out = plan(["tool", "not_yet_classified"])
        self.assertFalse(out["coverage_known"])
        self.assertEqual(out["unknown_scopes"], ["not_yet_classified"])
        with self.assertRaises(ValueError):
            plan([])


if __name__ == "__main__":
    unittest.main()
