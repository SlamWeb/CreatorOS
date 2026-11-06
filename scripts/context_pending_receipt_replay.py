"""Audit pending-receipt references against pinned local historical snapshots.

No model/business-tool calls and no historical content is saved. Job/account
authorization cannot be verified from these snapshots; only equality and
reference mechanics are tested. Use a fresh output path for each run.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from creatoros.ai.context import ModelContext
from creatoros.session.context_trace import detailed_breakdown
from creatoros.tools.model_projection import project_tool_messages
from scripts.context_attribution_baseline import SAMPLES, analyze_sample


def sample_audit(root, sample):
    label, sid, rid, *_ = sample
    analyze_sample(root, *sample)
    path = root / sid / "messages.request-trace" / (rid + ".json")
    context = json.loads(path.read_text(encoding="utf-8"))["context"]
    projected = project_tool_messages(context["messages"])
    calls, prior, counters = {}, {}, Counter()
    for old, new in zip(context["messages"], projected):
        for call in old.get("tool_calls") or []:
            function = call.get("function") or call
            calls[call.get("id")] = function
        if old.get("role") != "tool":
            continue
        call_id = old.get("tool_call_id")
        function = calls.get(call_id) or {}
        if function.get("name") != "get_skill_extraction":
            continue
        try:
            before, after = json.loads(old["content"]), json.loads(new["content"])
            arguments = json.loads(function.get("arguments", "{}"))
            if not all(isinstance(item, dict) for item in (before, after, arguments)):
                continue
            ref = after.get("result_ref")
            if isinstance(ref, dict):
                source = prior.get(ref.get("tool_call_id"))
                counters["references"] += 1
                counters["source_earlier"] += bool(source)
                counters["same_arguments"] += bool(source and source[0] == arguments)
                counters["same_raw_result"] += bool(source and source[1] == old["content"])
                counters["job_id_matches_response_id"] += (arguments.get("job_id") == before.get("id") == after.get("id"))
                counters["status_retained"] += before.get("status") == after.get("status")
                counters["no_error"] += not bool(before.get("error") or before.get("error_type"))
            prior[call_id] = (arguments, old["content"])
        except (ValueError, KeyError, TypeError, AttributeError):
            counters["unparseable"] += 1
    details = detailed_breakdown(ModelContext.from_messages(projected, context["tools"]), kind="main")
    return {"label": label, "total_estimate": sum(details["parts"].values()),
            "extraction_result_estimate": details["tool_results_by_tool"].get("get_skill_extraction", 0),
            "checks": dict(counters), "account_authorization_verified": False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sessions-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        raise FileExistsError("Output must not exist")
    results = [sample_audit(args.sessions_root, sample) for sample in SAMPLES]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"version": 1, "samples": results}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"samples": len(results), "references": sum(r["checks"].get("references", 0) for r in results)}))


if __name__ == "__main__":
    main()
