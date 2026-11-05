"""Opt-in DeepSeek check for same-request plan argument references.

Synthetic data only; no model or business tools execute unless explicitly opted
in, and even then the single model call cannot execute a tool. No files written.
"""
import argparse
import json
import os
from pathlib import Path
import secrets

from dotenv import load_dotenv

from creatoros.ai.context import ModelContext
from creatoros.ai.deepseek import DeepSeekProvider
from creatoros.tools.model_projection import project_tool_messages
from tests.test_plan_argument_context import call, plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Send one paid DeepSeek request")
    args = parser.parse_args()
    if not args.live:
        print("live_plan_argument_context=not_run pass --live to make one model request")
        return
    load_dotenv(dotenv_path=Path.cwd() / ".env")
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY is unavailable; no model call attempted")
    marker = "P" + secrets.token_hex(6).upper()
    body = ("背景资料" * 200) + "; 校验码:" + marker + ";" + ("参考资料" * 100)
    messages = [
        {"role": "system", "content": "你是一个只读诊断助手。按照历史工具回执回答问题。不要调用工具。"},
        {"role": "user", "content": "先查看计划。"},
        *call("p1", plan(body=body)),
        {"role": "user", "content": "再读取一次计划。"},
        *call("p2", plan(version=2, body=body, status="unknown")),
        {"role": "user", "content": "第二条计划回执的 step-a 步骤参数 question 中的校验码是什么？"
                                   "该步骤当前状态是什么？请只返回 校验码|状态。"},
    ]
    projected = project_tool_messages(messages)
    assert "arguments_ref" in json.loads(projected[-2]["content"])["steps"][0]
    schema = [{"type": "function", "function": {"name": "manage_plan", "description": "读取计划",
        "parameters": {"type": "object", "properties": {"action": {"type": "string"}}}}}]
    provider = DeepSeekProvider(key, model="deepseek-flash", timeout_seconds=90,
                                max_retries=0, reasoning_effort="none")
    response = provider.complete(ModelContext.from_messages(projected, schema))
    text = response.content or ""
    passed = marker in text and "unknown" in text.lower() and not response.tool_calls
    print(json.dumps({"live_plan_argument_context": "passed" if passed else "failed",
                      "usage": response.usage.to_dict() if response.usage else None,
                      "tool_calls": len(response.tool_calls)}, ensure_ascii=False))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
