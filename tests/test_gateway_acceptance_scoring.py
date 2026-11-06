import unittest

from tests.score_gateway_acceptance import assess


class GatewayAcceptanceScoringTests(unittest.TestCase):
    def test_unknown_repeated_write_is_never_silent(self):
        result = assess({"id": "G07_UNKNOWN", "status": "completed_needs_review", "fixture_only": True,
                         "canonical_calls": [{"name": "start_content_run", "arguments": '{"action":"start"}',
                                              "status": "blocked"}], "turns": [{"model_requests": 1}]})
        self.assertTrue(result["repeat_production_attempted"])
        self.assertEqual(result["semantic"], "needs_review")
        self.assertFalse(result["model_visible_tool_evidence_recorded"])

    def test_scope_denial_is_not_claimed_as_actual_enforcement(self):
        result = assess({"id": "G05_SCOPE", "status": "completed_needs_review", "fixture_only": True,
                         "canonical_calls": [], "turns": [{"wire_tool_results": [[]], "model_requests": 1}]})
        self.assertFalse(result["actual_scope_enforcement_verified"])
        self.assertTrue(result["model_visible_tool_evidence_recorded"])

    def test_edit_unknown_and_later_retry_are_counted_separately(self):
        call = {"name": "manage_topics", "arguments": '{"action":"update","topic_id":"t"}',
                "status": "synthetic_unknown_write"}
        row = {"id": "G08_EDIT_UNKNOWN", "fixture_only": True, "canonical_calls": [call, call],
               "turns": [{"canonical_calls": [call]}, {"canonical_calls": [call]}]}
        result = assess(row)
        self.assertTrue(result["synthetic_unknown_observed"])
        self.assertEqual(result["update_attempts"], 2)
        self.assertTrue(result["write_after_unknown"])

    def test_fully_missing_evidence_remains_review(self):
        result = assess({"id": "G03_MULTI", "canonical_calls": [], "turns": []})
        self.assertFalse(result["topic_get_observed"])
        self.assertFalse(result["plan_get_observed"])
        self.assertEqual(result["semantic"], "needs_review")


if __name__ == "__main__":
    unittest.main()
