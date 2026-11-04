"""Scoring contracts and counterexamples, without model or production writes."""
from copy import deepcopy
import unittest

from creatoros.evaluation.benchmark.grader import _expectation, MissingEvidence
from creatoros.evaluation.benchmark.scoring import aggregate_scores, score_case, validate_case_scoring, validate_judge


def case():
    return {"intent": "新增指定选题", "expected_outcome": "正确选题已入队", "evaluation_scope": "outcome",
            "scoring": {"program_checks": [{"id": "outcome_01", "label": "新增正确选题", "critical": False}],
                        "critical_checks": ["no_private_leak"]},
            "rubric": [{"id": "meaning", "question": "切入点原意是否保留？", "critical": False}]}


def grade(status="passed", gate="passed"):
    return {"checks": [{"id": "outcome_01", "status": status}, {"id": "no_private_leak", "status": gate},
                       {"id": "tool_budget", "status": "failed"}]}


def judge(value=True):
    return {"items": [{"id": "meaning", "pass": value, "evidence": "保存记录与用户要求一致"}]}


def world():
    return {"before": {"database": {"topics": [{"id": "old", "title": "keep", "series_id": "SA"}]}, "files": {"old.png": "sha"}},
            "after": {"database": {"topics": [{"id": "old", "title": "keep", "series_id": "SA"},
                {"id": "new", "title": "borrow 与 lend", "series_id": "SA", "status": "queued"}]}, "files": {"old.png": "sha"}},
            "variables": {"series_a": "SA"}}


class BenchmarkScoringTests(unittest.TestCase):
    def test_rsi_submission_and_score_do_not_prove_image_was_evaluated(self):
        from creatoros.evaluation.benchmark.server import rsi_round_status
        row = {"status": "completed", "round_count": 1, "best_score": 85,
               "active_round": {"production_submitted": True}}
        self.assertEqual(rsi_round_status([row]), "needs_review")
        self.assertEqual(rsi_round_status([{**row, "round_count": 2}]), "failed")
        self.assertEqual(rsi_round_status([{"status": "completed"}]), "needs_review")

    def test_program_semantics_separate_and_efficiency_not_business_score(self):
        result = score_case(case(), grade(), judge(False))
        self.assertEqual(result["program"]["score"], 100)
        self.assertEqual(result["semantic"]["score"], 0)
        self.assertIsNone(result["combined_score"])

    def test_missing_judge_not_zero_or_full_credit(self):
        result = score_case(case(), grade(), {"error": "network", "items": []})
        self.assertIsNone(result["semantic"]["score"])
        self.assertEqual(result["semantic"]["coverage"], 0)
        self.assertEqual(result["program"]["score"], 100)

    def test_partial_evidence_keeps_score_unknown_and_shows_coverage(self):
        task = case()
        task["scoring"]["program_checks"].append({"id": "outcome_02", "label": "未生产"})
        result = score_case(task, grade(), judge())
        self.assertIsNone(result["program"]["score"])
        self.assertEqual(result["program"]["coverage"], 0.5)
        self.assertEqual(result["program"]["observed_score"], 100)

    def test_gate_failure_cannot_hide_behind_full_scores(self):
        result = score_case(case(), grade(gate="failed"), judge())
        self.assertEqual(result["critical_status"], "failed")
        self.assertEqual(result["program"]["score"], 100)

    def test_duplicate_check_id_cannot_overwrite_failure_with_pass(self):
        data = grade("failed", "failed")
        data["checks"].extend([{"id": "outcome_01", "status": "passed"},
                               {"id": "no_private_leak", "status": "passed"}])
        result = score_case(case(), data, judge())
        self.assertIsNone(result["program"]["score"])
        self.assertEqual(result["critical_status"], "needs_review")

    def test_browser_failure_is_an_execution_gate_not_a_business_deduction(self):
        task = case()
        task["scoring"]["critical_checks"].append("browser_completed")
        data = grade()
        data["checks"].append({"id": "browser_completed", "status": "failed", "detail": "Refresh Trace returned 422"})
        result = score_case(task, data, judge())
        self.assertEqual(result["program"]["score"], 100)
        self.assertEqual(result["semantic"]["score"], 100)
        self.assertEqual(result["critical_status"], "failed")

    def test_duplicate_missing_and_unjustified_judge_items_rejected(self):
        for bad in ({"items": judge()["items"] * 2}, {"items": [{"id": "meaning", "pass": True}]},
                    {"items": [{"id": "meaning", "evidence": "missing explicit pass"}]},
                    {"items": [{"id": "meaning", "pass": "true", "evidence": "bad type"}]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_judge(case(), bad)

    def test_duplicate_scoring_ids_rejected(self):
        task = case()
        task["scoring"]["program_checks"] *= 2
        with self.assertRaises(ValueError):
            validate_case_scoring(task)

    def test_batch_averages_cases_not_number_of_rules(self):
        first = score_case(case(), grade(), judge())
        second = score_case(case(), grade("failed"), judge(False))
        second["program"].update(total=10, failed=10)
        result = aggregate_scores([{"scores": first}, {"scores": second}])
        self.assertEqual(result["program"]["score"], 50)
        self.assertIsNone(result["combined_score"])

    def test_unmeasured_case_and_old_protocol_cannot_be_averaged_away(self):
        result = aggregate_scores([{"scores": score_case(case(), grade(), judge())}, {"scores": None}])
        self.assertIsNone(result["program"]["score"])
        self.assertEqual(result["program"]["observed_score"], 100)
        self.assertEqual(result["program"]["coverage"], 0.5)

    def test_new_topic_requires_correct_content_not_just_count(self):
        exp = {"kind": "new_rows_match", "table": "topics", "value": [{"title": "borrow与lend", "series_id": "{series_a}", "status": "queued"}],
               "preserve_existing": True, "normalize_whitespace": True}
        data = world()
        self.assertTrue(_expectation(exp, data)[0])
        for field, value in (("title", "wrong"), ("series_id", "OTHER"), ("status", "skipped")):
            changed = deepcopy(data)
            changed["after"]["database"]["topics"][1][field] = value
            self.assertFalse(_expectation(exp, changed)[0])
        data["after"]["database"]["topics"][0]["title"] = "unexpected edit"
        self.assertFalse(_expectation(exp, data)[0])

    def test_net_one_does_not_hide_delete_one_add_two(self):
        data = world()
        data["after"]["database"]["topics"] = [{"id": "one", "title": "x"}, {"id": "two", "title": "x"}]
        exp = {"kind": "new_rows_match", "table": "topics", "value": [{"title": "x"}], "preserve_existing": True}
        self.assertFalse(_expectation(exp, data)[0])

    def test_overlapping_patterns_match_distinct_rows_without_greedy_error(self):
        data = world()
        data["after"]["database"]["topics"].append({"id": "another", "series_id": "SA", "title": "different"})
        exp = {"kind": "new_rows_match", "table": "topics", "value": [{"series_id": "SA"}, {"title": "borrow 与 lend"}]}
        self.assertTrue(_expectation(exp, data)[0])

    def test_unchanged_rows_cannot_ignore_deletion_or_wrong_identity(self):
        data = world()
        self.assertTrue(_expectation({"kind": "rows_unchanged", "table": "topics"}, data)[0])
        data["after"]["database"]["topics"].pop(0)
        self.assertFalse(_expectation({"kind": "rows_unchanged", "table": "topics"}, data)[0])
        self.assertTrue(_expectation({"kind": "rows_unchanged", "table": "topics", "ids": ["unknown"]}, data)[0])
        data["after"]["database"]["topics"].append({"id": "unknown"})
        self.assertFalse(_expectation({"kind": "rows_unchanged", "table": "topics", "ids": ["unknown"]}, data)[0])

    def test_exact_media_count_does_not_allow_excess_deliveries(self):
        data = {"background": {"images": ["1.png", "2.png"]}}
        self.assertFalse(_expectation({"kind": "background_count", "field": "images", "value": 1}, data)[0])

    def test_missing_sdk_events_are_not_proof_of_zero_image_generation(self):
        data = {"codex": {"parse_errors": [], "files": [], "threads": [], "event_counts": {},
                          "images": {"completed_generation_items": 0}}}
        rule = {"kind": "codex_image_count", "value": 0, "require_execution": True}
        with self.assertRaises(MissingEvidence):
            _expectation(rule, data)
        data["codex"]["generation_coverage"] = {"status": "no_execution_observed"}
        with self.assertRaises(MissingEvidence):
            _expectation(rule, data)
        self.assertTrue(_expectation({**rule, "require_execution": False}, data)[0])
        data["codex"]["generation_coverage"] = {"status": "complete"}
        self.assertTrue(_expectation(rule, data)[0])


if __name__ == "__main__":
    unittest.main()
