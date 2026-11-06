import unittest

from creatoros.evaluation.comparison import CONTRACT_FIELDS, compare_runs


def run(run_id, rows, **changes):
    meta = {key: f"same-{key}" for key in CONTRACT_FIELDS}
    meta.update(changes)
    return {"id": run_id, "metadata": meta, "planned_cases": ["A", "B", "C"], "results": rows}


def result(case_id, status, tokens, *, definition="same", scope="provider_actual_agent"):
    return {"case_id": case_id, "status": status, "case_definition_hash": definition, "round_attempt": 1,
            "evidence": {"execution_status": "completed"},
            "metrics": {"input_tokens": tokens, "usage_scope": scope, "elapsed_seconds": 1,
                        "latency_scope": "case_wall", "tool_calls": 1, "tool_calls_scope": "agent"}}


class ComparisonTests(unittest.TestCase):
    def test_comparison_excludes_missing_and_unknown_without_hiding_them(self):
        a = run("a", [result("A", "failed", 20), result("B", "needs_review", 80)])
        b = run("b", [result("A", "passed", 10), result("B", "passed", None)])
        out = compare_runs(a, b)
        self.assertEqual(out["common_planned"], 3)
        self.assertEqual(out["outcomes"], {"paired_decidable": 1, "baseline_passed": 0, "candidate_passed": 1})
        self.assertEqual(out["metrics"]["input_tokens"], {"paired_cases": 1, "baseline_mean": 20, "candidate_mean": 10})
        self.assertEqual(out["cases"][2]["reason"], "missing_result")

    def test_incompatible_contract_or_case_never_produces_score_deltas(self):
        a = run("a", [result("A", "passed", 1)])
        for b in (run("b", [result("A", "passed", 2)], judge_hash="different"),
                  run("b", [result("A", "passed", 2, definition="different")])):
            out = compare_runs(a, b)
            self.assertEqual(out["outcomes"]["paired_decidable"], 0)
            self.assertIsNone(out["metrics"]["input_tokens"]["candidate_mean"])

    def test_estimates_and_missing_contract_not_presented_as_real_usage(self):
        a = run("a", [result("A", "passed", 1, scope="estimate")])
        b = run("b", [result("A", "passed", 2, scope="estimate")])
        self.assertEqual(compare_runs(a, b)["metrics"]["input_tokens"]["paired_cases"], 0)
        del b["metadata"]["fixture_hash"]
        self.assertFalse(compare_runs(a, b)["contract_compatible"])

    def test_unknown_or_different_attempt_does_not_claim_comparability(self):
        a = run("a", [result("A", "passed", 1)])
        b = run("b", [result("A", "passed", 2)])
        b["results"][0]["round_attempt"] = 2
        self.assertEqual(compare_runs(a, b)["cases"][0]["reason"], "different_attempt")
        del b["results"][0]["round_attempt"]
        self.assertEqual(compare_runs(a, b)["cases"][0]["reason"], "missing_attempt_identity")
        a["results"][0]["round_attempt"] = 2
        b["results"][0]["round_attempt"] = 2
        self.assertEqual(compare_runs(a, b)["cases"][0]["reason"], "repeat_attempt_not_predeclared")

    def test_estimated_cost_requires_matching_pricing_and_complete_bounds(self):
        a = run("a", [result("A", "passed", 1)])
        b = run("b", [result("A", "passed", 2)])
        for row, low, high in ((a["results"][0], 0.1, 0.2), (b["results"][0], 0.05, 0.1)):
            row["metrics"]["cost"] = {"currency": "USD", "source": "pricing", "estimated": True,
                                      "status": "complete", "minimum": low, "maximum": high}
        metrics = compare_runs(a, b)["metrics"]
        self.assertEqual(metrics["estimated_cost_min_usd"]["paired_cases"], 1)
        self.assertEqual(metrics["estimated_cost_max_usd"]["candidate_mean"], 0.1)
        b["results"][0]["metrics"]["cost"]["status"] = "partial"
        self.assertEqual(compare_runs(a, b)["metrics"]["estimated_cost_min_usd"]["paired_cases"], 0)

    def test_unknown_execution_never_contributes_to_efficiency_metrics(self):
        a = run("a", [result("A", "passed", 10)])
        b = run("b", [result("A", "unknown", 0)])
        b["results"][0]["evidence"]["execution_status"] = "failed"
        self.assertEqual(compare_runs(a, b)["metrics"]["input_tokens"]["paired_cases"], 0)


if __name__ == "__main__":
    unittest.main()
