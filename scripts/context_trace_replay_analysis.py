"""Read-only replay of six fixed local request histories; no API or business tools.

Only numeric attribution and tool names leave this process. Snapshot/trace pins
come from context_attribution_baseline; unavailable inputs fail closed. Results
are estimates over actual historical requests, never a claim about task success.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from creatoros.ai.context import ModelContext, estimate_tokens
from creatoros.session.context_trace import detailed_breakdown
from creatoros.tools.model_projection import project_tool_messages
from scripts.context_attribution_baseline import SAMPLES, analyze_sample


def _exact_repeat_stats(messages):
    """Exact duplicates only; no assertion that duplicate content is expendable."""
    result_counts, argument_counts = Counter(), Counter()
    result_sizes, argument_sizes = {}, {}
    for message in messages:
        if message.get("role") == "tool" and isinstance(message.get("content"), str):
            value = message["content"]
            key = hashlib.sha256(value.encode("utf-8")).digest()
            result_counts[key] += 1
            result_sizes[key] = estimate_tokens(value)
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                function = call.get("function") or call
                value = function.get("arguments")
                if isinstance(value, (str, dict)):
                    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
                    key = hashlib.sha256(serialized.encode("utf-8")).digest()
                    argument_counts[key] += 1
                    argument_sizes[key] = estimate_tokens(value)
    return {
        "exact_duplicate_tool_result_messages": sum(n - 1 for n in result_counts.values() if n > 1),
        "exact_duplicate_tool_result_estimate": sum((n - 1) * result_sizes[k] for k, n in result_counts.items() if n > 1),
        "exact_duplicate_argument_calls": sum(n - 1 for n in argument_counts.values() if n > 1),
        "exact_duplicate_argument_estimate": sum((n - 1) * argument_sizes[k] for k, n in argument_counts.items() if n > 1),
    }


def analyze_history(sessions_root, sample):
    label, session_id, target_id, *_ = sample
    fixed = analyze_sample(sessions_root, *sample)  # verify target and trace prefix before use
    root = sessions_root / session_id
    records, missing = [], 0
    turn_labels = {}
    for line in (root / "messages.context-trace.jsonl").read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("event") != "finished" or row.get("request_kind") != "main":
            continue
        usage, request_id = row.get("usage") or {}, row.get("request_id")
        if not isinstance(usage.get("input_tokens"), int) or not isinstance(request_id, str):
            continue
        turn = row.get("turn_id")
        if turn not in turn_labels:
            turn_labels[turn] = len(turn_labels) + 1
        path = root / "messages.request-trace" / f"{request_id}.json"
        if not path.exists():
            missing += 1
        else:
            source = json.loads(path.read_text(encoding="utf-8"))["context"]
            original = ModelContext.from_messages(source["messages"], source["tools"])
            current = ModelContext.from_messages(project_tool_messages(source["messages"]), source["tools"])
            d0, d1 = detailed_breakdown(original, kind="main"), detailed_breakdown(current, kind="main")
            records.append({
                "request_sequence": len(records) + missing + 1,
                "user_turn_sequence": turn_labels[turn],
                "input_tokens_actual": usage["input_tokens"],
                "output_tokens_actual": usage.get("output_tokens"),
                "cache_hit_tokens_actual": usage.get("cache_hit_tokens"),
                "cache_miss_tokens_actual": usage.get("cache_miss_tokens"),
                "input_estimate_recorded_projection": sum(d0["parts"].values()),
                "input_estimate_current_result_projection": sum(d1["parts"].values()),
                "parts_estimate": d0["parts"],
                "tool_results_by_tool_estimate": d0["tool_results_by_tool"],
                "exact_repeat": _exact_repeat_stats(source["messages"]),
            })
        if request_id == target_id:
            break
    if not records or records[-1]["input_tokens_actual"] != fixed["actual_input_tokens"]:
        raise ValueError("target history missing")
    return {"label": label, "model": fixed["model"], "target_snapshot_sha256": fixed["snapshot_sha256"],
            "trace_prefix_sha256": fixed["trace_prefix_sha256"],
            "missing_request_snapshots": missing, "requests": records}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sessions-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        raise FileExistsError("Output must not exist")
    data = {"schema_version": 1, "description": "read-only historical request snapshot projections",
            "samples": [analyze_history(args.sessions_root, sample) for sample in SAMPLES]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"samples": len(data["samples"]), "requests": sum(len(x["requests"]) for x in data["samples"]),
                      "missing": sum(x["missing_request_snapshots"] for x in data["samples"])}))


if __name__ == "__main__":
    main()
