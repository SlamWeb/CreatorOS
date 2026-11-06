"""Opt-in no-tool-dispatch replay of an existing sent, image-free DeepSeek request.

This sends the real historical request contents to DeepSeek again. Only run
when explicitly authorized. No business tool is called; model replies are not
executed or written. The report contains usage and bounded structural facts.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
import time

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from creatoros.ai.context import ModelContext
from creatoros.ai.deepseek import DeepSeekProvider
from creatoros.tools.model_projection import project_tool_messages
from scripts.context_attribution_baseline import SAMPLES, analyze_sample


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--sessions-root", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if not args.live:
        print("No model calls without --live")
        return
    if args.report.exists():
        raise FileExistsError("Report must not exist")
    sample = next(s for s in SAMPLES if s[0] == "mixed_short")
    label, sid, rid, *_ = sample
    original_trace = analyze_sample(args.sessions_root, *sample)
    raw = json.loads((args.sessions_root / sid / "messages.request-trace" / (rid + ".json")).read_text(encoding="utf-8"))
    source = raw["context"]
    if any(block.get("type") in {"image_ref", "image_url"}
           for message in source["messages"] if isinstance(message.get("content"), list)
           for block in message["content"] if isinstance(block, dict)):
        raise ValueError("image input is outside this probe")
    key = (dotenv_values(args.env_file).get("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        raise ValueError("missing model credential")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = {"version": 1, "status": "running", "sample": label,
              "snapshot_sha256": original_trace["snapshot_sha256"],
              "model": "deepseek-flash", "reasoning_effort": "none",
              "output_cap": 256, "business_tool_calls_executed": 0,
              "source_original_input_tokens": original_trace["actual_input_tokens"],
              "source_original_output_tokens": original_trace["actual_output_tokens"],
              "attempts": []}
    def save():
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    save()
    provider = DeepSeekProvider(key, model="deepseek-flash", reasoning_effort="none",
                                timeout_seconds=180, max_retries=0)
    try:
        for label, messages in (("recorded", source["messages"]),
                                ("current_projection", project_tool_messages(source["messages"]))):
            entry = {"variant": label, "status": "started"}
            report["attempts"].append(entry)
            save()
            context = replace(ModelContext.from_messages(messages, source["tools"]), max_output_tokens=256)
            started = time.monotonic()
            try:
                result = provider.complete(context)
                entry.update(status="succeeded", usage=result.usage.to_dict() if result.usage else None,
                             proposed_tool_call_count=len(result.tool_calls or []),
                             reply_present=bool(result.content))
            except Exception as exc:
                entry.update(status="failed", error_type=type(exc).__name__)
            finally:
                entry["elapsed_seconds"] = round(time.monotonic() - started, 3)
                save()
        report["status"] = "complete"
    finally:
        provider.client.close()
        save()
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
