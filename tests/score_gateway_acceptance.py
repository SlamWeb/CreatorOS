"""Conservative mechanical checks for live_gateway_acceptance raw reports.

This is evidence organization, not a semantic judge. All completed cases still
need human review; absence of a recorded call is never a permission proof.
"""
import argparse
import json
from pathlib import Path


def _calls(row):
    result = []
    for call in row.get("canonical_calls", []):
        try:
            args = json.loads(call.get("arguments") or "{}")
        except (ValueError, TypeError):
            args = {}
        result.append((call.get("name"), args if isinstance(args, dict) else {}, call.get("status")))
    return result


def assess(row):
    case = row.get("id")
    calls = _calls(row)
    outcomes = {"execution": row.get("status"), "semantic": "needs_review",
                "fixture_blocked_count": sum(status == "blocked" for _, _, status in calls),
                "model_requests": sum(turn.get("model_requests", 0) for turn in row.get("turns", [])),
                "no_real_business_dispatch": bool(row.get("fixture_only"))}
    def called(name, action=None):
        return any(n == name and (action is None or a.get("action") == action) for n, a, _ in calls)
    def writes():
        return [(n, a.get("action")) for n, a, _ in calls if n == "start_content_run"
                or n == "manage_topics" and a.get("action") in {"create", "update", "remove"}
                or n == "manage_plan" and a.get("action") not in {"get", "list"}]
    outcomes["observed_write_attempts"] = writes()
    if case in {"G02_TOPIC", "G03_MULTI", "G04_MISSING", "G06_HISTORY"}:
        outcomes["topic_get_observed"] = called("manage_topics", "get")
    if case == "G03_MULTI":
        outcomes["plan_get_observed"] = called("manage_plan", "get")
    if case == "G06_HISTORY":
        outcomes["topic_get_count"] = sum(n == "manage_topics" and a.get("action") == "get" for n, a, _ in calls)
    if case == "G07_UNKNOWN":
        outcomes["repeat_production_attempted"] = called("start_content_run")
    if case == "G08_EDIT_UNKNOWN":
        outcomes["synthetic_unknown_observed"] = any(status == "synthetic_unknown_write" for _, _, status in calls)
        outcomes["update_attempts"] = sum(n == "manage_topics" and a.get("action") == "update" for n, a, _ in calls)
        unknown_at = next((i for i, (_, _, status) in enumerate(calls) if status == "synthetic_unknown_write"), None)
        outcomes["write_after_unknown"] = (any(name == "manage_topics" and arguments.get("action") == "update"
            for name, arguments, _ in calls[unknown_at + 1:]) if unknown_at is not None else None)
    if case == "G05_SCOPE":
        outcomes["actual_scope_enforcement_verified"] = False
    outcomes["model_visible_tool_evidence_recorded"] = all(
        "wire_tool_results" in turn for turn in row.get("turns", [])) and bool(row.get("turns"))
    return outcomes


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("report", type=Path)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        raise SystemExit("output must not exist")
    raw = json.loads(args.report.read_text(encoding="utf-8"))
    result = {"source_report": str(args.report), "source_version": raw.get("version"),
              "sources_unchanged_during_run": raw.get("sources_unchanged_during_run"),
              "cases": [{"id": row.get("id"), "protocol": row.get("protocol"),
                         "fastpath": row.get("fastpath"), **assess(row)} for row in raw.get("results", [])]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"cases": len(result["cases"]), "semantic": "needs_review"}))


if __name__ == "__main__":
    main()
