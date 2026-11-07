"""Conservative, paired comparisons of immutable evaluation runs."""
from __future__ import annotations

import math


# Product code is deliberately excluded: it is the thing being compared.
CONTRACT_FIELDS = (
    "dataset_sha256", "fixture_hash", "judge_hash", "scoring_hash",
    "driver_hash", "execution_mode", "model", "trial_policy",
)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def compare_runs(baseline, candidate):
    """Compare matching cases only; absence or conflicting contracts never means pass."""
    bm, cm = baseline.get("metadata") or {}, candidate.get("metadata") or {}
    reasons = []
    for key in CONTRACT_FIELDS:
        left, right = bm.get(key), cm.get(key)
        if not isinstance(left, str) or not left.strip() or not isinstance(right, str) or not right.strip():
            reasons.append({"field": key, "reason": "missing"})
        elif left != right:
            reasons.append({"field": key, "reason": "different"})
    if bm.get("execution_mode") in {"local-dispatch-v4", "local-dispatch-v5", "local-dispatch-v6", "local-dispatch-v7"} or cm.get("execution_mode") in {"local-dispatch-v4", "local-dispatch-v5", "local-dispatch-v6", "local-dispatch-v7"}:
        left, right = bm.get("source_namespace"), cm.get("source_namespace")
        def source_valid(metadata):
            allowed = ({"base", "natural"} if metadata.get("execution_mode") == "local-dispatch-v4" else {"e06-scope"} if metadata.get("execution_mode") == "local-dispatch-v6" else {"expansion"} if metadata.get("execution_mode") == "local-dispatch-v7" else {"base", "natural", "expansion"})
            return metadata.get("source_namespace") in allowed
        if not source_valid(bm) or not source_valid(cm):
            reasons.append({"field": "source_namespace", "reason": "missing"})
        elif left != right:
            reasons.append({"field": "source_namespace", "reason": "different"})

    left_results = {item["case_id"]: item for item in baseline.get("results", [])}
    right_results = {item["case_id"]: item for item in candidate.get("results", [])}
    left_planned, right_planned = set(baseline.get("planned_cases", [])), set(candidate.get("planned_cases", []))
    shared = sorted(left_planned & right_planned)
    pairs = []
    for case_id in shared:
        left, right = left_results.get(case_id), right_results.get(case_id)
        why = None
        if left is None or right is None:
            why = "missing_result"
        elif not left.get("case_definition_hash") or not right.get("case_definition_hash"):
            why = "missing_case_definition"
        elif left["case_definition_hash"] != right["case_definition_hash"]:
            why = "different_case_definition"
        elif not isinstance(left.get("round_attempt"), int) or not isinstance(right.get("round_attempt"), int):
            why = "missing_attempt_identity"
        elif left["round_attempt"] != right["round_attempt"]:
            why = "different_attempt"
        elif left["round_attempt"] != 1:
            why = "repeat_attempt_not_predeclared"
        elif reasons:
            why = "incompatible_run_contract"
        pairs.append({"case_id": case_id, "comparable": why is None, "reason": why,
                      "baseline_status": left.get("status") if left else "missing_result",
                      "candidate_status": right.get("status") if right else "missing_result",
                      "baseline_attempt": left.get("round_attempt") if left else None,
                      "candidate_attempt": right.get("round_attempt") if right else None})

    comparable_ids = {pair["case_id"] for pair in pairs if pair["comparable"]}
    decidable = [pair for pair in pairs if pair["comparable"]
                 and pair["baseline_status"] in {"passed", "failed"}
                 and pair["candidate_status"] in {"passed", "failed"}]
    metrics = {}
    def complete_case(row):
        evidence = row.get("evidence") or {}
        return (row.get("status") in {"passed", "failed", "needs_review"}
                and evidence.get("execution_status") in {"completed", "succeeded"})
    for key in ("input_tokens", "output_tokens", "elapsed_seconds", "tool_calls"):
        values = []
        for case_id in sorted(comparable_ids):
            if not complete_case(left_results[case_id]) or not complete_case(right_results[case_id]):
                continue
            lm, rm = left_results[case_id].get("metrics") or {}, right_results[case_id].get("metrics") or {}
            if key in {"input_tokens", "output_tokens"}:
                scope_key = "usage_scope"
            elif key == "elapsed_seconds":
                scope_key = "latency_scope"
            else:
                scope_key = "tool_calls_scope"
            scope = lm.get(scope_key)
            if not isinstance(scope, str) or not scope or scope != rm.get(scope_key):
                continue
            if key in {"input_tokens", "output_tokens"} and scope != "provider_actual_agent":
                continue
            if _number(lm.get(key)) and _number(rm.get(key)):
                values.append((lm[key], rm[key]))
        count = len(values)
        metrics[key] = {"paired_cases": count,
                        "baseline_mean": sum(x for x, _ in values) / count if count else None,
                        "candidate_mean": sum(y for _, y in values) / count if count else None}
    cost_pairs = []
    for case_id in sorted(comparable_ids):
        if not complete_case(left_results[case_id]) or not complete_case(right_results[case_id]):
            continue
        left_cost = (left_results[case_id].get("metrics") or {}).get("cost")
        right_cost = (right_results[case_id].get("metrics") or {}).get("cost")
        if not isinstance(left_cost, dict) or not isinstance(right_cost, dict):
            continue
        if any(cost.get("estimated") is not True or cost.get("status") != "complete"
               or cost.get("currency") != "USD" or not cost.get("source") for cost in (left_cost, right_cost)):
            continue
        if left_cost["source"] != right_cost["source"]:
            continue
        if not all(_number(cost.get(key)) for cost in (left_cost, right_cost) for key in ("minimum", "maximum")):
            continue
        if any(cost["minimum"] > cost["maximum"] for cost in (left_cost, right_cost)):
            continue
        cost_pairs.append((left_cost, right_cost))
    for key in ("minimum", "maximum"):
        count = len(cost_pairs)
        metrics["estimated_cost_" + ("min" if key == "minimum" else "max") + "_usd"] = {
            "paired_cases": count,
            "baseline_mean": sum(left[key] for left, _ in cost_pairs) / count if count else None,
            "candidate_mean": sum(right[key] for _, right in cost_pairs) / count if count else None,
        }
    return {
        "baseline_run_id": baseline.get("id"), "candidate_run_id": candidate.get("id"),
        "contract_compatible": not reasons, "contract_differences": reasons,
        "baseline_planned": len(left_planned), "candidate_planned": len(right_planned),
        "common_planned": len(shared), "comparable_cases": len(comparable_ids),
        "outcomes": {"paired_decidable": len(decidable),
                     "baseline_passed": sum(p["baseline_status"] == "passed" for p in decidable),
                     "candidate_passed": sum(p["candidate_status"] == "passed" for p in decidable)},
        "metrics": metrics, "cases": pairs,
    }
