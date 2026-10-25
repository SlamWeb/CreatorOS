"""Compare only outbound projections of immutable real request snapshots.

No model calls, business writes or history compaction. Git's baseline is trusted
local project code, evaluated separately so its globals cannot affect the app.
"""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

from creatoros.ai.context import ContextBudget, ModelContext
from creatoros.tools.model_projection import project_tool_messages


def compare(path, baseline):
    original = path.read_bytes()
    snapshot = json.loads(original)
    raw = snapshot["context"]
    messages = deepcopy(raw["messages"])
    before = baseline(messages)
    after = project_tool_messages(messages)
    assert messages == raw["messages"], "projection changed its source"
    assert len(before) == len(after)
    changes, names = Counter(), {}
    for old, new in zip(before, after):
        assert old.get("role") == new.get("role")
        if old.get("role") != "tool":
            assert old == new, "changed user input, tool arguments or reasoning"
        else:
            assert old.get("tool_call_id") == new.get("tool_call_id")
        for call in old.get("tool_calls", []):
            names[call["id"]] = call.get("name") or call.get("function", {}).get("name")
        if old.get("role") == "tool" and old != new:
            changes[names.get(old.get("tool_call_id"), "unknown")] += 1
    tools = deepcopy(raw["tools"])
    before_tokens = ContextBudget.from_context(ModelContext.from_messages(before, tools)).estimated_input_tokens
    after_tokens = ContextBudget.from_context(ModelContext.from_messages(after, tools)).estimated_input_tokens
    assert tools == raw["tools"], "tool definitions changed"
    assert path.read_bytes() == original, "source Trace was modified"
    return {"session": path.parent.parent.name, "request_id": snapshot["request_id"],
            "measurement": "local_estimate_not_provider_usage", "before": before_tokens, "after": after_tokens,
            "saved": before_tokens - after_tokens,
            "reduction_percent": round(100 * (before_tokens - after_tokens) / before_tokens, 2),
            "changed_result_counts": dict(changes), "source_sha256": hashlib.sha256(original).hexdigest(),
            "messages_arguments_reasoning_preserved": True, "source_unchanged": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshots", nargs="+", type=Path)
    parser.add_argument("--baseline", default="bba63c6")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source = subprocess.check_output(["git", "show", args.baseline + ":creatoros/tools/model_projection.py"], encoding="utf-8")
    namespace = {}
    exec(compile(source, "baseline-model-projection", "exec"), namespace)
    report = {"baseline_commit": args.baseline,
              "results": [compare(path, namespace["project_tool_messages"]) for path in args.snapshots]}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
