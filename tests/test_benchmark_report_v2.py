"""Pure first-attempt report aggregation on temporary evidence files."""
import json
from pathlib import Path
import tempfile
import unittest

from creatoros.evaluation.benchmark.report import summarize_batch


PRICING = {"currency": "USD", "frozen_at": "2026-10-31", "source": "test snapshot",
    "models": {"deepseek-flash": {"cache_hit_per_million": 0.003,
        "cache_miss_per_million": 0.15, "output_per_million": 0.6}}}


def request(identifier="r1", **changes):
    return {"request_id": identifier, "purpose": "agent", "model": "deepseek-flash", "input_tokens": 100,
        "output_tokens": 10, "cache_hit_tokens": 80, "cache_miss_tokens": 20, "ttft_ms": 100.0,
        "first_tool_ms": None, "elapsed_seconds": 1.0, **changes}


class BenchmarkReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="benchmark-report-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dataset = self.root / "cases.json"
        self.dataset.write_text(json.dumps({"dataset_id": "test-only", "cases": [
            {"id": f"B{n:02}", "title": f"Task {n}", "category": "security" if n <= 5 else "tools",
             "split": "acceptance" if n <= 10 else "dev", "family": f"family-{n}"} for n in range(1, 51)]}), encoding="utf-8")
        self.counter = 0
        self.plan()

    def plan(self, ids=None):
        (self.root / "manifest.json").write_text(json.dumps({"pricing": PRICING,
            "selected_case_ids": ids or [f"B{n:02}" for n in range(1, 51)]}), encoding="utf-8")

    def record(self, case="B01", requests=None, **changes):
        self.counter += 1
        directory = self.root / "records" / f"{self.counter:032x}"
        directory.mkdir(parents=True)
        report = {"case_id": case, "auto_status": "passed", "semantic_status": "passed",
            "execution_status": "completed", "error": None, "checks": [],
            "dimensions": {"boundary_enforced": "passed"}, "trajectory": {"denied_model_calls": 0}, **changes}
        (directory / "report.json").write_text(json.dumps(report), encoding="utf-8")
        (directory / "requests.json").write_text(json.dumps([] if requests is None else requests), encoding="utf-8")
        return directory

    def summarize(self):
        return summarize_batch(self.root, self.dataset)

    def test_missing_cases_remain_in_denominator_and_links_are_written(self):
        directory = self.record(requests=[request()])
        result = self.summarize()
        self.assertEqual((result["planned"], result["with_results"], result["missing"]), (50, 1, 49))
        self.assertEqual(result["pass_at_1"], {"passed": 1, "denominator": 50, "value": 0.02})
        self.assertEqual(result["overall_status"], "needs_review")
        self.assertIsNone(result["metrics"]["all_deepseek"]["cost"]["minimum"])
        self.assertIsNone(result["pass_power_k"])
        text = (self.root / "RESULTS.md").read_text(encoding="utf-8")
        self.assertIn(f"records/{directory.name}/report.json", text)
        self.assertIn(f"records/{directory.name}/requests.json", text)
        self.assertEqual(len([row for row in text.splitlines() if row.startswith("| B")]), 50)
        self.assertTrue((self.root / "summary.json").is_file())

    def test_manifest_subset_has_explicit_scope_and_subset_denominator(self):
        self.plan(["B11", "B12"])
        self.record("B11")
        result = self.summarize()
        self.assertEqual(result["dataset_case_count"], 50)
        self.assertEqual(result["execution_scope"], "subset")
        self.assertEqual(result["pass_at_1"]["denominator"], 2)
        self.assertEqual(result["by_split"]["dev"]["missing"], 1)

    def test_mixed_cache_is_token_weighted_peak_is_max_and_latencies_have_n(self):
        self.plan(["B11", "B12"])
        self.record("B11", requests=[request(input_tokens=100, cache_hit_tokens=80, cache_miss_tokens=20)])
        self.record("B12", requests=[request(input_tokens=900, cache_hit_tokens=90, cache_miss_tokens=810, ttft_ms=300)])
        result = self.summarize()
        metric = result["metrics"]["agent"]
        self.assertEqual(metric["usage"]["input_tokens"], 1000)
        self.assertEqual(metric["usage"]["peak_input_tokens"], 900)
        self.assertAlmostEqual(metric["cache"]["hit_ratio"], 0.17)
        self.assertEqual(metric["latency"]["ttft_ms"]["observed_count"], 2)
        self.assertEqual(metric["latency"]["ttft_ms"]["p50"], 200)
        self.assertEqual(metric["latency"]["ttft_ms"]["p95"], 290)
        self.assertEqual(result["overall_status"], "passed")

    def test_unknown_usage_keeps_cost_and_total_unknown_not_zero(self):
        self.plan(["B11"])
        self.record("B11", requests=[request(), request("r2", input_tokens=None, output_tokens=None,
            cache_hit_tokens=None, cache_miss_tokens=None, ttft_ms=None)])
        metric = self.summarize()["metrics"]["all_deepseek"]
        self.assertIsNone(metric["usage"]["input_tokens"])
        self.assertEqual(metric["usage"]["observed_input_tokens"], 100)
        self.assertIsNone(metric["cost"]["minimum"])
        self.assertIsNone(metric["cache"]["hit_ratio"])
        self.assertEqual(metric["latency"]["ttft_ms"]["observed_count"], 1)

    def test_security_failure_cannot_hide_behind_top_level_pass(self):
        self.plan(["B01", "B11"])
        self.record("B01", checks=[{"id": "no_private_leak", "status": "failed"}])
        self.record("B11")
        result = self.summarize()
        self.assertEqual(result["passed"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["safety"]["status"], "failed")
        self.assertEqual(result["overall_status"], "failed")
        self.assertEqual(result["safety"]["failures"][0]["failed_checks"], ["no_private_leak"])

    def test_multiple_trials_are_failed_not_best_of_selected(self):
        self.plan(["B11"])
        first = self.record("B11", auto_status="failed", error={"kind": "HTTPError", "message": "sensitive raw detail"})
        second = self.record("B11", auto_status="passed")
        result = self.summarize()
        self.assertEqual(result["pass_at_1"]["value"], 0)
        self.assertEqual(result["duplicate_trials"][0]["selected_best"], False)
        row = result["cases"][0]
        self.assertEqual(row["observed_error"], "duplicate_trial_policy_failed")
        self.assertEqual(row["record_paths"], [f"records/{first.name}/report.json", f"records/{second.name}/report.json"])
        self.assertTrue((first / "report.json").is_file())

    def test_request_identity_dedup_keeps_purpose_and_case_distinct(self):
        self.plan(["B11", "B12"])
        self.record("B11", requests=[request(), request(), request(purpose="judge")])
        self.record("B12", requests=[request()])
        result = self.summarize()
        self.assertEqual(result["duplicate_request_records_removed"], 1)
        self.assertEqual(result["metrics"]["agent"]["model_calls"], 2)
        self.assertEqual(result["metrics"]["judge"]["model_calls"], 1)
        self.assertEqual(result["metrics"]["all_deepseek"]["model_calls"], 3)

    def test_missing_request_file_does_not_turn_missing_cost_into_free(self):
        self.plan(["B11"])
        directory = self.record("B11")
        (directory / "requests.json").unlink()
        result = self.summarize()
        self.assertIsNone(result["metrics"]["agent"]["cost"]["minimum"])
        self.assertEqual(result["metrics"]["agent"]["cost"]["status"], "evidence_missing")
        self.assertEqual(result["overall_status"], "needs_review")

    def test_missing_semantic_verdict_remains_needs_review_and_error_is_not_diagnosed(self):
        self.plan(["B11"])
        directory = self.record("B11")
        report = json.loads((directory / "report.json").read_text())
        del report["semantic_status"]
        (directory / "report.json").write_text(json.dumps(report), encoding="utf-8")
        result = self.summarize()
        self.assertEqual(result["needs_review"], 1)
        self.assertEqual(result["cases"][0]["root_cause_attribution"], "pending")
        self.assertIsNone(result["codex"]["subscription_cost"])

    def test_conflicting_request_copies_keep_usage_unknown(self):
        self.plan(["B11"])
        self.record("B11", requests=[request(), request(input_tokens=200, cache_miss_tokens=120)])
        result = self.summarize()
        self.assertEqual(len(result["request_conflicts"]), 1)
        self.assertIsNone(result["metrics"]["all_deepseek"]["usage"]["input_tokens"])
        self.assertEqual(result["overall_status"], "needs_review")

    def test_missing_boundary_evidence_is_not_a_success(self):
        self.plan(["B11"])
        self.record("B11", dimensions={})
        result = self.summarize()
        self.assertEqual(result["passed"], 0)
        self.assertEqual(result["needs_review"], 1)
        self.assertEqual(result["safety"]["status"], "needs_review")

    def test_unplanned_result_does_not_raise_subset_success_or_hide_its_cost(self):
        self.plan(["B11"])
        self.record("B11", requests=[request()])
        self.record("B12", requests=[request()])
        result = self.summarize()
        self.assertEqual(result["planned"], 1)
        self.assertEqual(result["unexpected_case_ids"], ["B12"])
        self.assertEqual(result["metrics"]["all_deepseek"]["model_calls"], 2)
        self.assertEqual(result["overall_status"], "needs_review")

    def test_codex_cumulative_thread_snapshots_and_copies_are_not_added(self):
        self.plan(["B11", "B12"])
        first, second = self.record("B11"), self.record("B12")
        for directory, counter in ((first, 100), (second, 180)):
            doc = {"threads": ["thread-a"], "attempts": [{"id": directory.name, "output_directory": "outputs/attempt-1", "producer_thread_id": "thread-a"}],
                "usage": {"by_thread": [{"thread_id": "thread-a", "usage": {"input_tokens": counter,
                    "cached_input_tokens": 50, "output_tokens": 20, "reasoning_output_tokens": 5},
                    "observations": [{"usage": {"input_tokens": 90, "output_tokens": 10}}]}],
                    "total": {"input_tokens": counter}}}
            (directory / "codex.json").write_text(json.dumps(doc), encoding="utf-8")
        result = self.summarize()
        self.assertEqual(result["codex"]["observed_thread_count"], 1)
        self.assertEqual(result["codex"]["total"]["input_tokens"], 180)
        self.assertEqual(result["codex"]["total"]["output_tokens"], 20)
        self.assertEqual(len(result["codex"]["attempt_refs"]), 2)
        self.assertEqual(result["metrics"]["all_deepseek"]["model_calls"], 0)
        self.assertIsNone(result["codex"]["subscription_cost"])

    def test_codex_missing_usage_stays_null_and_rsi_is_a_separate_ds_group(self):
        self.plan(["B11"])
        directory = self.record("B11", requests=[request(purpose="rsi")])
        doc = {"threads": ["known-thread", "missing-thread"], "usage": {"by_thread": [
            {"thread_id": "known-thread", "usage": {"input_tokens": 100, "output_tokens": 10}},
            {"thread_id": "missing-thread", "usage": None}]}}
        (directory / "codex.json").write_text(json.dumps(doc), encoding="utf-8")
        result = self.summarize()
        self.assertIsNone(result["codex"]["total"]["input_tokens"])
        self.assertEqual(result["codex"]["observed_known_total"]["input_tokens"], 100)
        self.assertEqual(result["codex"]["coverage"]["input_tokens"]["missing_threads"], 1)
        self.assertEqual(result["metrics"]["rsi"]["model_calls"], 1)
        self.assertEqual(result["metrics"]["agent"]["model_calls"], 0)
        self.assertEqual(result["metrics"]["all_deepseek"]["model_calls"], 1)
        self.assertEqual(result["unclassified_request_records"], 0)
        self.assertIn("Codex SDK累计用量", (self.root / "RESULTS.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
