"""Inspect and explicitly run frozen regression views, never silently skip cases."""
from __future__ import annotations

import argparse
import hashlib
import json
import re

from creatoros.evaluation.regression_catalog import catalog


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


_MEMBER_FIELDS = ("instance_id", "case_sha256", "resource_class", "execution_readiness", "adapter")


def _membership(suite):
    return {"id": suite["id"], "version": suite["version"],
            "cases": [{key: row[key] for key in _MEMBER_FIELDS} for row in suite["cases"]],
            "candidates": [{key: row[key] for key in _MEMBER_FIELDS} for row in suite["candidates"]]}


def prepare(suite_id, selected_instance_ids=None, change_scopes=None):
    from creatoros.evaluation.change_rules import plan
    scopes = change_scopes if change_scopes is not None else ["unknown"]
    plan(scopes)  # validates input; unknown scopes remain explicitly uncovered.
    data = catalog()
    suite = next((s for s in data["suites"] if s["id"] == suite_id), None)
    if suite is None:
        raise ValueError("Unknown regression suite")
    source = data.get("catalog_source")
    membership = _membership(suite)
    if not isinstance(source, dict) or _digest(source) != data["catalog_sha256"] or _digest(membership) != suite["membership_sha256"]:
        raise ValueError("Catalog source or membership digest mismatch")
    by_id = {row["instance_id"]: row for row in suite["cases"]}
    planned = list(by_id)
    selected = planned if selected_instance_ids is None else list(selected_instance_ids)
    if not selected or any(not isinstance(item, str) for item in selected) or len(selected) != len(set(selected)):
        raise ValueError("Select a nonempty unique list of suite instances")
    if any(item not in by_id for item in selected):
        raise ValueError("Selected instance is not a suite member")
    blocked = [{"instance_id": item, "reason": by_id[item].get("reason")}
               for item in selected if by_id[item].get("execution_readiness") != "enabled"
               or by_id[item].get("adapter") not in {"tool-calling-v1", "local-dispatch-v1"}
               or not item.startswith("base:")]
    # A view with unclassified candidates is partial even when all its current
    # admitted members are selected; never call that the full routine scope.
    complete = selected == planned and not blocked and suite.get("scope_status") == "complete"
    binding = {
        "catalog_suite_id": suite["id"], "catalog_suite_version": suite["version"],
        "catalog_sha256": data["catalog_sha256"], "membership_sha256": suite["membership_sha256"],
        "planned_instance_ids": planned, "selected_instance_ids": selected,
        "selection_kind": "complete" if complete else "partial",
        "scope_status": suite.get("scope_status", "partial"),
        "pending_candidates": suite["counts"]["pending_candidates"],
        "unclassified_candidates": suite["counts"]["unclassified_candidates"],
        "change_scopes": scopes,
        "frozen_catalog": {"source": source, "membership": membership},
    }
    return {"catalog_binding": binding, "blocked": blocked,
            "selected_cases": [by_id[item]["case_id"] for item in selected],
            # All catalog runs use the new guard, even where the old weaker
            # tool-only adapter happened to allow the same source case.
            "requires_local_dispatch": True}


def validate_binding(case_ids, binding):
    if not isinstance(binding, dict):
        raise ValueError("Invalid catalog binding")
    prepared = prepare(binding.get("catalog_suite_id"), binding.get("selected_instance_ids"), binding.get("change_scopes"))
    if prepared["catalog_binding"] != binding or prepared["blocked"] or prepared["selected_cases"] != list(case_ids):
        raise ValueError("Catalog binding, readiness or selected cases changed")
    return {"catalog_binding": binding}


def validate_frozen_binding(case_ids, binding):
    """Validate the run's bound snapshot without reading today's catalog."""
    from creatoros.evaluation.change_rules import plan
    if not isinstance(binding, dict) or set(binding) != {"catalog_suite_id", "catalog_suite_version", "catalog_sha256",
            "membership_sha256", "planned_instance_ids", "selected_instance_ids", "selection_kind", "scope_status",
            "pending_candidates", "unclassified_candidates", "change_scopes", "frozen_catalog"}:
        raise ValueError("Invalid frozen catalog binding")
    frozen = binding["frozen_catalog"]
    if not isinstance(frozen, dict) or set(frozen) != {"source", "membership"}:
        raise ValueError("Missing frozen catalog snapshot")
    source, membership = frozen["source"], frozen["membership"]
    if not isinstance(source, dict) or not isinstance(membership, dict):
        raise ValueError("Invalid frozen catalog snapshot")
    for key in ("catalog_sha256", "membership_sha256"):
        if not isinstance(binding[key], str) or not re.fullmatch(r"[0-9a-f]{64}", binding[key]):
            raise ValueError("Invalid frozen catalog digest")
    if _digest(source) != binding["catalog_sha256"] or _digest(membership) != binding["membership_sha256"]:
        raise ValueError("Frozen catalog snapshot digest differs")
    if membership.get("id") != binding["catalog_suite_id"] or membership.get("version") != binding["catalog_suite_version"]:
        raise ValueError("Frozen suite identity differs")
    members, candidates = membership.get("cases"), membership.get("candidates")
    if not isinstance(members, list) or not isinstance(candidates, list) or not members:
        raise ValueError("Invalid frozen suite membership")
    if any(not isinstance(row, dict) or set(row) != set(_MEMBER_FIELDS) for row in members + candidates):
        raise ValueError("Malformed frozen member")
    ids = [row["instance_id"] for row in members]
    selected = binding["selected_instance_ids"]
    if (ids != binding["planned_instance_ids"] or len(ids) != len(set(ids)) or not isinstance(selected, list)
            or not selected or len(selected) != len(set(selected)) or any(x not in ids for x in selected)
            or len(candidates) != binding["pending_candidates"]
            or sum(row.get("resource_class") == "unknown" for row in candidates) != binding["unclassified_candidates"]):
        raise ValueError("Frozen suite selection differs")
    by_id = {row["instance_id"]: row for row in members}
    if any(not isinstance(x, str) or not x.startswith("base:") or by_id[x]["execution_readiness"] != "enabled"
           or by_id[x]["adapter"] not in {"tool-calling-v1", "local-dispatch-v1"} for x in selected):
        raise ValueError("Frozen selected instance was not executable")
    if [x.removeprefix("base:") for x in selected] != list(case_ids):
        raise ValueError("Frozen selected cases differ")
    scope = "partial" if candidates or any(row["execution_readiness"] != "enabled" for row in members) else "complete"
    complete = selected == ids and scope == "complete"
    if binding["scope_status"] != scope or binding["selection_kind"] != ("complete" if complete else "partial"):
        raise ValueError("Frozen coverage classification differs")
    plan(binding["change_scopes"])
    return {"catalog_binding": binding}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["plan", "run"])
    parser.add_argument("--suite", required=True)
    parser.add_argument("--instances", help="Explicit comma-separated instance IDs; default is all members")
    parser.add_argument("--allow-partial", action="store_true", help="Acknowledge incomplete suite coverage")
    parser.add_argument("--allow-paid", action="store_true", help="Explicitly enable live model calls")
    parser.add_argument("--change-scope", action="append", help="Declare change scope (repeatable); required for formal run")
    parser.add_argument("--round-id")
    parser.add_argument("--title")
    parser.add_argument("--code-version")
    parser.add_argument("--branch")
    parser.add_argument("--parent-round-id")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    selected = args.instances.split(",") if args.instances else None
    if args.action == "run" and not args.change_scope:
        parser.error("Run requires an explicit --change-scope")
    prepared = prepare(args.suite, selected, args.change_scope)
    if args.action == "plan":
        print(json.dumps(prepared, ensure_ascii=False, indent=2))
        return 0
    if prepared["blocked"]:
        parser.error("Selected cases are not executable; inspect the plan")
    if prepared["catalog_binding"]["selection_kind"] != "complete" and not args.allow_partial:
        parser.error("Incomplete coverage requires --allow-partial")
    if not all((args.round_id, args.title, args.code_version, args.run_id)):
        parser.error("Run requires round-id, title, code-version and run-id")
    from creatoros.evaluation.registry import EvalRegistry
    from creatoros.evaluation.tool_calling import run_registered
    result = run_registered(EvalRegistry(), round_data={"id": args.round_id, "title": args.title,
        "code_version": args.code_version, "branch": args.branch, "parent_round_id": args.parent_round_id,
        "merge_status": "developing", "changes": args.change_scope}, run_id=args.run_id,
        cases=prepared["selected_cases"], allow_paid=args.allow_paid, local_dispatch=prepared["requires_local_dispatch"],
        catalog_binding=prepared["catalog_binding"])
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
