"""Opt-in isolated A/B for identical pending receipt projection.

All history is synthetic. Model tool calls are blocked, no business tools run.
"""
import argparse
import json
import subprocess
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from types import ModuleType
from unittest.mock import patch
from uuid import uuid4

from dotenv import dotenv_values

from creatoros.agent import loop
from creatoros.ai.deepseek import DeepSeekProvider
from creatoros.context import RuntimeContext
from creatoros.session.context_trace import read_trace
from creatoros.session.snapshot import load_messages, save_messages
from creatoros.terminal import Console
from creatoros.tools.results import ToolResult
from creatoros.web.chat import ACCOUNT_INSTRUCTIONS


ROOT = Path(__file__).resolve().parents[1]


def old_projection():
    source = subprocess.check_output(["git", "show", "e2f6607:creatoros/tools/model_projection.py"],
                                     cwd=ROOT, text=True, encoding="utf-8")
    module = ModuleType("creatoros.tools._old_pending_projection")
    module.__package__ = "creatoros.tools"
    exec(compile(source, "e2f6607:model_projection.py", "exec"), module.__dict__)
    return module.project_tool_messages


def history(nonce):
    note = "这是后台提取任务的合成工作说明。" * 100 + "校对要求：逐段核对证据。"
    receipt = json.dumps({"id": "job-test", "status": "running", "operation": "extract", "error": None,
                          "note": note, "revision": 1}, ensure_ascii=False)
    messages = [{"role": "system", "content": "测试隔离标识 " + nonce + "。\n" + ACCOUNT_INSTRUCTIONS},
                {"role": "user", "content": "请帮我查看之前的 Skill 提取任务。"}]
    for number in range(8):
        messages.extend([
            {"role": "assistant", "content": "", "tool_calls": [{"id": f"call-{number}",
                "name": "get_skill_extraction", "arguments": json.dumps({"job_id": "job-test"})}]},
            {"role": "tool", "tool_call_id": f"call-{number}", "content": receipt},
        ])
    return messages


def run_case(key, name, projector):
    result = {"variant": name, "status": "failed", "requests": [], "blocked_tool_calls": []}
    provider = DeepSeekProvider(key, model="deepseek-flash", reasoning_effort="none", timeout_seconds=90, max_retries=0)
    with TemporaryDirectory(prefix="creatoros-pending-receipt-") as directory:
        root = Path(directory)
        session = root / "messages.json"
        save_messages(history(uuid4().hex), session)
        question = "刚才的技能提取任务是什么状态？回执里的校对要求是什么？能确定最近一次读取是实时查询吗？"
        inputs = iter([question, "/exit"])
        started = monotonic()
        def block(call, **kwargs):
            result["blocked_tool_calls"].append(call.name)
            return ToolResult("隔离测试不允许发起业务查询。", True, "fixture_blocked")
        try:
            with patch.object(loop, "project_tool_messages", projector), patch.object(loop, "execute_tool_call", side_effect=block):
                loop.run_agent(provider, session_file=session, require_existing_session=True,
                               runtime_context=RuntimeContext(root, archive_only_reads=True,
                                                              allowed_tools=frozenset({"read_file"})),
                               console=Console(input_fn=lambda _: next(inputs), output=StringIO()), max_turns=6)
            messages = load_messages(session, require_existing=True)
            result["answer"] = next((m.get("content") for m in reversed(messages)
                                     if m.get("role") == "assistant" and m.get("content") and not m.get("tool_calls")), "")
            result["status"] = "completed_needs_review"
        except Exception as exc:
            result["error_type"] = type(exc).__name__
        finally:
            result["elapsed_seconds"] = round(monotonic() - started, 3)
            result["requests"] = [{"kind": row.get("request_kind"), "status": row.get("status"), "usage": row.get("usage")}
                for row in read_trace(session, limit=100)["items"] if row.get("event") == "finished"]
            provider.client.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--env-file", type=Path, default=Path("D:/CreatorOS/.env"))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not args.live:
        print("No model calls without --live")
        return
    if not args.report or args.report.exists():
        raise SystemExit("A new --report path is required")
    key = (dotenv_values(args.env_file).get("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        raise SystemExit("DEEPSEEK_API_KEY_missing")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    results = []
    for name, projector in [("old", old_projection()), ("new", loop.project_tool_messages)]:
        results.append(run_case(key, name, projector))
        args.report.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
