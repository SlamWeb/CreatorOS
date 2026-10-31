"""Pure DTO conversion checks; no model, DB or service calls."""
import copy
import unittest

from creatoros.evaluation.benchmark.metrics import aggregate_metrics


def request(identifier="r1", **changes):
    return {"request_id": identifier, "case_id": "A01", "turn_index": 1,
            "model": "deepseek-v4-flash", "input_tokens": 100, "output_tokens": 20,
            "cache_hit_tokens": 80, "cache_miss_tokens": 20,
            "elapsed_seconds": 2.0, "ttft_ms": 100.0, "first_tool_ms": None,
            "error": None, **changes}


PRICING = {"currency": "USD", "frozen_at": "2026-10-31", "source": "official frozen snapshot",
           "aliases": {"deepseek-v4-flash": "deepseek-flash"},
           "models": {"deepseek-flash": {"cache_hit_per_million": [0.003, 0.006],
                                        "cache_miss_per_million": [0.15, 0.3],
                                        "output_per_million": [0.6, 1.2]}}}


class BenchmarkMetricsTests(unittest.TestCase):
    def test_accumulated_input_is_separate_from_peak_and_weighted_cache(self):
        rows = [request(), request("r2", input_tokens=900, output_tokens=30,
                                  cache_hit_tokens=90, cache_miss_tokens=810, ttft_ms=300)]
        metrics = aggregate_metrics(rows)
        self.assertEqual(metrics["model_calls"], 2)
        self.assertEqual(metrics["usage"]["input_tokens"], 1000)
        self.assertEqual(metrics["usage"]["peak_input_tokens"], 900)
        self.assertEqual(metrics["usage"]["output_tokens"], 50)
        self.assertAlmostEqual(metrics["cache"]["hit_ratio"], 0.17)
        self.assertEqual(metrics["latency"]["ttft_ms"]["p50"], 200)
        self.assertEqual(metrics["latency"]["ttft_ms"]["p95"], 290)
        self.assertIsNone(metrics["latency"]["first_tool_ms"]["p50"])

    def test_missing_usage_keeps_totals_null_and_observed_sum_explicit(self):
        result = aggregate_metrics([request(), request("failed", input_tokens=None,
            output_tokens=None, cache_hit_tokens=None, cache_miss_tokens=None,
            ttft_ms=None, error={"kind": "timeout"})])
        self.assertIsNone(result["usage"]["input_tokens"])
        self.assertEqual(result["usage"]["observed_input_tokens"], 100)
        self.assertEqual(result["error_calls"], 1)
        self.assertEqual(result["usage_status"], "partial")
        self.assertEqual(result["latency"]["ttft_ms"]["coverage"], 0.5)
        self.assertIsNone(result["cache"]["hit_ratio"])
        self.assertEqual(result["cache"]["observed_hit_ratio"], 0.8)
        self.assertIsNone(result["cache"]["token_coverage"])

    def test_missing_cache_buckets_have_coverage_and_price_bounds(self):
        result = aggregate_metrics([request(cache_hit_tokens=None, cache_miss_tokens=None)], PRICING)
        self.assertIsNone(result["cache"]["hit_ratio"])
        self.assertEqual(result["cache"]["request_coverage"], 0)
        self.assertAlmostEqual(result["cost"]["minimum"], (100 * .003 + 20 * .6) / 1e6)
        self.assertAlmostEqual(result["cost"]["maximum"], (100 * .3 + 20 * 1.2) / 1e6)

    def test_cost_uses_passed_frozen_rates_aliases_and_no_bill_claim(self):
        rows, rates = [request()], copy.deepcopy(PRICING)
        original = copy.deepcopy(rows)
        result = aggregate_metrics(rows, rates)
        self.assertAlmostEqual(result["cost"]["minimum"], (80 * .003 + 20 * .15 + 20 * .6) / 1e6)
        self.assertAlmostEqual(result["cost"]["maximum"], result["cost"]["minimum"] * 2)
        self.assertTrue(result["cost"]["estimated"])
        self.assertEqual(result["cost"]["source"], rates["source"])
        self.assertEqual(rows, original)
        self.assertEqual(rates, PRICING)

    def test_partial_cost_does_not_price_unknown_usage_or_codex(self):
        rows = [request(), request("missing", input_tokens=None, output_tokens=None,
                                  cache_hit_tokens=None, cache_miss_tokens=None),
                request("sdk", model={"name": "gpt-6.1-luna", "provider": "Codex"})]
        cost = aggregate_metrics(rows, PRICING)["cost"]
        self.assertEqual(cost["status"], "partial")
        self.assertIsNone(cost["minimum"])
        self.assertIsNotNone(cost["observed_minimum"])
        self.assertEqual(cost["priced_calls"], 1)
        self.assertEqual(cost["unpriced_calls"], 1)
        self.assertEqual(cost["codex"]["calls"], 1)
        self.assertIsNone(cost["codex"]["amount"])

    def test_zero_calls_zero_usage_but_no_latency_or_cache_rate(self):
        result = aggregate_metrics([])
        self.assertEqual(result["usage"]["input_tokens"], 0)
        self.assertEqual(result["usage_status"], "no_calls")
        self.assertIsNone(result["usage"]["peak_input_tokens"])
        self.assertIsNone(result["latency"]["ttft_ms"]["p95"])
        self.assertIsNone(result["cache"]["hit_ratio"])
        self.assertEqual(result["cost"]["minimum"], 0)

    def test_invalid_values_duplicate_requests_and_cumulative_usage_fail(self):
        for changes in ({"input_tokens": True}, {"input_tokens": -1},
                        {"output_tokens": 1.5}, {"ttft_ms": float("nan")},
                        {"cache_hit_tokens": 110}, {"usage_scope": "thread_cumulative"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                aggregate_metrics([request(**changes)])
        with self.assertRaises(ValueError):
            aggregate_metrics([request(), request()])

    def test_context_estimate_and_schema_peaks_are_not_provider_usage(self):
        result = aggregate_metrics([request(context_estimated_tokens=190, tool_schema_tokens=30),
            request("r2", context_estimated_tokens=250, tool_schema_tokens=40)])
        self.assertEqual(result["usage"]["input_tokens"], 200)
        self.assertEqual(result["usage"]["peak_input_tokens"], 100)
        self.assertEqual(result["usage"]["peak_context_estimated_tokens"], 250)
        self.assertEqual(result["usage"]["peak_tool_schema_tokens"], 40)

    def test_incomplete_cache_accounting_and_unknown_models_stay_explicit(self):
        result = aggregate_metrics([request(cache_hit_tokens=10, cache_miss_tokens=10,
                                            model="unpriced-model")], PRICING)
        self.assertIsNone(result["cache"]["hit_ratio"])
        self.assertEqual(result["cache"]["observed_calls"], 0)
        self.assertEqual(result["cost"]["status"], "unavailable")
        self.assertIsNone(result["cost"]["maximum"])


if __name__ == "__main__":
    unittest.main()
