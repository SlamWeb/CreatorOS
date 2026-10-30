"""Opt-in real Codex probe: rejected tool call, repair, then persisted resume."""
import argparse
import asyncio
import json
import tempfile
from pathlib import Path
from time import monotonic

from openai_codex import ApprovalMode, Sandbox

from creatoros.integrations.codex import (
    CODEX_EFFORT, CODEX_MODEL, CodexDynamicTool, _bounded_sdk, _production_client,
)


async def probe(root):
    calls = []

    async def submit(arguments):
        result = ({"accepted": True, "receipt_id": "probe-accepted"}
                  if arguments.get("path") == "ready.png" else
                  {"accepted": False, "error": "file_not_found", "correct_path": "ready.png"})
        calls.append({"arguments": arguments, "result": result})
        (root / "calls.json").write_text(json.dumps(calls, indent=2), encoding="utf-8")
        return result

    tool = CodexDynamicTool("submit_artifacts", "Submit the probe path; repair any rejected path.",
        {"type": "object", "properties": {"path": {"type": "string"}},
         "required": ["path"], "additionalProperties": False}, submit)
    reports = []
    thread_id = None
    for resume in (False, True):
        deadline = monotonic() + 180
        async with _production_client(deadline, dynamic_tools=[tool]) as client:
            options = dict(approval_mode=ApprovalMode.deny_all, cwd=str(root), model=CODEX_MODEL,
                sandbox=Sandbox.read_only, developer_instructions="Only call submit_artifacts. Do not use shell, images, web or other tools.")
            thread = await _bounded_sdk(client.thread_resume(thread_id, **options) if resume else
                                        client.thread_start(**options), deadline)
            thread_id = thread.id
            prompt = ("This is a transport probe, no actual files are required. Call submit_artifacts with path ready.png once."
                      if resume else "This is a transport fault-injection probe. First call submit_artifacts with path missing.png. "
                      "Then use its rejection response to correct the path and submit again. No actual files are required.")
            turn = await _bounded_sdk(thread.turn(prompt + " Only after accepted=true, reply DONE.",
                model=CODEX_MODEL, effort=CODEX_EFFORT, sandbox=Sandbox.read_only), deadline)
            result = await _bounded_sdk(turn.run(), deadline)
            reports.append({"thread_id": thread.id, "turn_id": result.id,
                            "final": result.final_response, "usage": str(result.usage)})
    assert [x["arguments"]["path"] for x in calls] == ["missing.png", "ready.png", "ready.png"], calls
    assert len({x["thread_id"] for x in reports}) == 1
    report = {"status": "passed", "calls": calls, "turns": reports,
              "scope": "real model and tool RPC; artifact validation is an injected fixture, no production or images"}
    (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if not args.run:
        parser.error("Real model usage requires --run")
    root = Path(tempfile.mkdtemp(prefix="dynamic-submission-", dir="tmp")).resolve()
    asyncio.run(probe(root))
