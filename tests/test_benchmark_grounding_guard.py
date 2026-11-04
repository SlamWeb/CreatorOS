"""Research source claims cannot be inferred from a bare link or model assertion."""
import unittest

from creatoros.evaluation.benchmark.grounding_guard import qualify_grounding_judgments


class GroundingGuardTests(unittest.TestCase):
    def test_uncorroborated_research_claim_remains_unknown_and_raw_result_preserved(self):
        for value in (True, False):
            raw = {"items": [{"id":"b48-s1", "pass":value, "evidence":"source asserted"},
                             {"id":"b48-s2", "pass":True, "evidence":"one candidate"}]}
            result = qualify_grounding_judgments({"id":"B48"}, raw)
            self.assertIsNone(result["items"][0]["pass"])
            self.assertIs(result["items"][1]["pass"], True)
            self.assertIs(raw["items"][0]["pass"], value)

    def test_other_cases_unchanged(self):
        raw = {"items":[{"id":"b48-s1", "pass":True, "evidence":"example"}]}
        self.assertEqual(qualify_grounding_judgments({"id":"B47"}, raw), raw)
