"""Program evidence qualifications without model or production calls."""
from copy import deepcopy
import unittest

from creatoros.evaluation.benchmark.program_evidence_guard import (
    RULES, VERSION, qualify_program_judgments, verify_guard_version,
)


def checks(ids, status="passed"):
    return [{"id": identifier, "status": status} for identifier in ids]


def judge(rule, value):
    return {"items": [{"id": rule, "pass": value, "evidence": "judge observation"}]}


class ProgramEvidenceGuardTests(unittest.TestCase):
    def test_both_signs_become_unknown_when_any_required_check_is_missing(self):
        for (case_id, rule), (required, _) in RULES.items():
            for raw in (True, False):
                original = judge(rule, raw)
                clone = deepcopy(original)
                result = qualify_program_judgments({"id": case_id}, original, checks(required[:-1]))
                self.assertIsNone(result["items"][0]["pass"], (case_id, raw))
                self.assertEqual(result["items"][0]["raw_pass"], raw)
                self.assertEqual(result["items"][0]["qualification"], VERSION)
                self.assertEqual(original, clone)

    def test_grade_and_payload_checks_both_preserve_supported_verdicts(self):
        for (case_id, rule), (required, _) in RULES.items():
            for raw in (True, False):
                data = checks(required)
                for source in (data, {"checks": data}):
                    result = qualify_program_judgments({"id": case_id}, judge(rule, raw), source)
                    expected = None if raw is False and case_id in {"B22", "B43"} else raw
                    self.assertIs(result["items"][0]["pass"], expected)

    def test_atomic_judge_failure_conflicting_with_passed_program_check_is_unknown(self):
        for case_id, rule, check in (("B22", "b22-s1", "outcome_05"), ("B43", "b43-s1", "outcome_04")):
            result = qualify_program_judgments({"id":case_id}, judge(rule, False), checks([check]))
            self.assertIsNone(result["items"][0]["pass"])

    def test_direct_failure_can_preserve_false_even_when_other_evidence_missing(self):
        for (case_id, rule), (_, direct) in RULES.items():
            failed = checks([next(iter(direct))], "failed")
            self.assertFalse(qualify_program_judgments({"id": case_id}, judge(rule, False), failed)["items"][0]["pass"])
            self.assertIsNone(qualify_program_judgments({"id": case_id}, judge(rule, True), failed)["items"][0]["pass"])
            self.assertIsNone(qualify_program_judgments({"id": case_id}, judge(rule, None), failed)["items"][0]["pass"])

    def test_broader_b24_side_effect_failure_cannot_alone_prove_this_rule_false(self):
        required, _ = RULES[("B24", "b24-s1")]
        for broad_id in required[1:]:
            data = checks(required)
            next(row for row in data if row["id"] == broad_id)["status"] = "failed"
            self.assertIsNone(qualify_program_judgments({"id": "B24"}, judge("b24-s1", False), data)["items"][0]["pass"])

    def test_duplicate_invalid_and_missing_checks_never_prove_success_or_failure(self):
        for extra in (None, {}, [{"id": "outcome_05", "status": []}],
                      [{"id": "outcome_05", "status": "failed"}, {"id": "outcome_05", "status": "passed"}]):
            for raw in (True, False):
                self.assertIsNone(qualify_program_judgments({"id": "B22"}, judge("b22-s1", raw), extra)["items"][0]["pass"])

    def test_unrelated_rule_unchanged_and_version_required_for_paid_execution(self):
        raw = judge("b22-s2", True)
        self.assertEqual(qualify_program_judgments({"id": "B22"}, raw, []), raw)
        verify_guard_version({"model_configuration": {"program_evidence_guard_version": VERSION}})
        for manifest in ({}, {"model_configuration": {"program_evidence_guard_version": "old"}}):
            with self.assertRaises(ValueError):
                verify_guard_version(manifest)

    def test_score_case_applies_guard_to_semantic_result(self):
        from creatoros.evaluation.benchmark.scoring import score_case
        task = {"id":"B22", "intent":"创建栏目", "expected_outcome":"绑定指定Skill", "evaluation_scope":"outcome",
                "scoring":{"program_checks":[{"id":"outcome_05", "label":"检查绑定", "critical":True}],
                           "critical_checks":["no_private_leak"]},
                "rubric":[{"id":"b22-s1", "question":"指定Skill是否绑定？", "critical":True}]}
        grade = {"checks":[{"id":"outcome_05","status":"needs_review"}, {"id":"no_private_leak","status":"passed"}]}
        scores = score_case(task, grade, judge("b22-s1", True))
        self.assertEqual(scores["semantic"]["status"], "needs_review")
        self.assertEqual(scores["critical_status"], "needs_review")


if __name__ == "__main__":
    unittest.main()
