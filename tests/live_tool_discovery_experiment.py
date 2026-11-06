"""Opt-in DeepSeek Chat Completions tool-discovery prototype A/B.

Synthetic fixtures; no actual business handler is dispatched. Discovery is a
client-side experiment, not a DeepSeek built-in. The original account schemas
and host parameter validation are reused without modifying production code.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from time import monotonic
from uuid import uuid4

from dotenv import dotenv_values

from creatoros.ai.context import ModelContext, estimate_tokens
from creatoros.ai.deepseek import DeepSeekProvider
from creatoros.context import RuntimeContext
from creatoros.tools import tool_registry, tools
from creatoros.tools.host_contract import bind_account_arguments, model_tool_schemas
from creatoros.web.chat import ACCOUNT_INSTRUCTIONS, ACCOUNT_TOOLS


ROOT = Path(__file__).resolve().parents[1]
CASES = [
    {"id": "TOPIC_READ", "query": "请看一下选题 topic-demo-18 的制作要求是什么？", "expected": "manage_topics", "required": {"action": "get", "topic_id": "topic-demo-18"}, "answer_hint": "上班族"},
    {"id": "PLAN_READ", "query": "计划 plan-demo-17 现在是什么状态？", "expected": "manage_plan", "required": {"action": "get", "plan_id": "plan-demo-17"}, "answer_hint": "进行中"},
    {"id": "SKILL_READ", "query": "knowledge-to-carousel 的 Skill 正文里写了什么？", "expected": "get_producer_skill", "required": {"skill_id": "knowledge-to-carousel"}, "answer_hint": "具体场景"},
    {"id": "FAILED_TASKS", "query": "请看看当前账号有哪些失败的制作任务。", "expected": "get_creator_tasks", "required": {"statuses": ["failed"]}, "answer_hint": "制作测试"},
    {"id": "NO_TOOL", "query": "你好，接下来一起整理创作思路吧。", "expected": None, "required": {}, "answer_hint": None},
    {"id": "AMBIGUOUS_WRITE", "query": "把这个栏目改个名字，改成小城日常。", "expected": None, "required": {}, "answer_hint": None},
]


def discovery_tools(schemas):
    catalog = "\n".join(f"{t['function']['name']}: {t['function'].get('description', '')[:75]}"
                         for t in schemas)
    return [
        {"type": "function", "function": {
            "name": "discover_tools",
            "description": "需要工具时，先从目录选出相关工具名称获取完整说明。仅在需要时调用；不执行任何业务操作。目录：\n" + catalog,
            "parameters": {"type": "object", "properties": {"names": {"type": "array", "minItems": 1,
                "maxItems": 4, "items": {"type": "string", "enum": [t['function']['name'] for t in schemas]}}},
                "required": ["names"], "additionalProperties": False}}},
        {"type": "function", "function": {
            "name": "call_discovered_tool",
            "description": "调用本轮已 discover_tools 返回完整说明的工具；严格按照该说明填入 arguments。仅调用获得授权的工具。",
            "parameters": {"type": "object", "properties": {
                "name": {"type": "string", "description": "已发现的工具名。"},
                "arguments": {"type": "object", "description": "所选工具的参数对象。"}},
                "required": ["name", "arguments"], "additionalProperties": False}}},
    ]


def fixture(tool, args):
    if tool == "manage_topics":
        return {"action": "get", "topic": {"id": "topic-demo-18", "title": "早餐",
                                         "brief": "给上班族的早餐故事。"}}
    if tool == "manage_plan":
        return {"id": "plan-demo-17", "status": "running", "message": "进行中"}
    if tool == "get_producer_skill":
        return {"skill_id": "knowledge-to-carousel", "path": "SKILL.md",
                "content": "从具体场景开始，解释一个知识点。", "digest": "synthetic-digest"}
    if tool == "get_creator_tasks":
        return {"items": [{"id": "run-demo-11", "title": "制作测试", "status": "failed"}],
                "page": {"total": 1, "has_more": False}}
    return {"fixture_error": "本实验只提供固定只读替身；未执行其他工具。"}


def compare_prefix(previous, current):
    if previous is None:
        return None
    old_messages, old_tools = previous
    new_messages, new_tools = current
    return {"same_tools": old_tools == new_tools,
            "messages_append_only": old_messages == new_messages[:len(old_messages)]}


def attempt(case, variant, key, schemas, scope):
    report = {"case_id": case["id"], "variant": variant, "status": "failed", "requests": [],
              "tool_calls": [], "fixture_calls": [], "answer": None, "error_type": None}
    client = DeepSeekProvider(key, model="deepseek-flash", reasoning_effort="none", timeout_seconds=90, max_retries=0)
    schema_by_name = {t["function"]["name"]: t for t in schemas}
    exposed = schemas if variant == "baseline" else discovery_tools(schemas)
    report["estimated_tool_schema_tokens"] = estimate_tokens(exposed)
    nonce = uuid4().hex
    messages = [{"role": "system", "content": "隔离测试 " + nonce + "。\n" + ACCOUNT_INSTRUCTIONS},
                {"role": "user", "content": case["query"]}]
    known = set()
    previous = None
    try:
        for step in range(6):
            context = ModelContext.from_messages(messages, exposed)
            wire = (client._to_openai_messages(context.to_request()[0]), context.to_request()[1])
            start = monotonic()
            response = client.complete(context)
            report["requests"].append({"index": step + 1, "usage": response.usage.to_dict() if response.usage else None,
                "seconds": round(monotonic() - start, 3), "prefix_from_previous": compare_prefix(previous, wire),
                "finish_reason": response.finish_reason})
            previous = deepcopy(wire)
            messages.append(response.to_message())
            if not response.tool_calls:
                report["answer"] = response.content
                report["status"] = "completed_needs_review"
                break
            for call in response.tool_calls:
                row = {"name": call.name, "arguments": call.arguments}
                report["tool_calls"].append(row)
                try:
                    args = json.loads(call.arguments)
                    if not isinstance(args, dict):
                        raise ValueError("arguments must be object")
                    if variant == "discovery" and call.name == "discover_tools":
                        names = args.get("names")
                        if not isinstance(names, list) or not 1 <= len(names) <= 4 or any(n not in schema_by_name for n in names):
                            raise ValueError("invalid discovery")
                        known.update(names)
                        output = {"tools": [schema_by_name[n] for n in names],
                                  "notice": "可调用这些工具，仍需满足用户授权和账号范围。"}
                    else:
                        actual = args.get("name") if variant == "discovery" and call.name == "call_discovered_tool" else call.name
                        actual_args = args.get("arguments") if variant == "discovery" and call.name == "call_discovered_tool" else args
                        if variant == "discovery" and (call.name != "call_discovered_tool" or actual not in known):
                            raise ValueError("tool was not discovered")
                        if actual not in schema_by_name or not isinstance(actual_args, dict):
                            raise ValueError("tool not allowed")
                        raw = json.dumps(actual_args, ensure_ascii=False)
                        bound = bind_account_arguments(actual, raw, scope)
                        parsed = tool_registry[actual].parse_arguments(bound)
                        row.update(resolved_name=actual, resolved_arguments=actual_args, validated=True)
                        if actual != case["expected"]:
                            output = {"fixture_error": "隔离替身不执行或模拟该操作。"}
                            row["fixture_blocked"] = True
                        else:
                            output = fixture(actual, parsed)
                            report["fixture_calls"].append(actual)
                except Exception as exc:
                    row.update(validated=False, error_type=type(exc).__name__)
                    output = {"fixture_error": "参数、工具范围或发现状态不正确，未执行。"}
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": json.dumps(output, ensure_ascii=False)})
        else:
            report["status"] = "step_limit"
    except Exception as exc:
        report["error_type"] = type(exc).__name__
    finally:
        client.client.close()
    expected = case["expected"]
    actual_calls = [r for r in report["tool_calls"] if r.get("resolved_name")]
    matches = [r for r in actual_calls if r["resolved_name"] == expected and all(
        r.get("resolved_arguments", {}).get(k) == v for k, v in case["required"].items())]
    report["structural_checks"] = {
        "expected_action_found": bool(matches) if expected else not actual_calls,
        "fixture_answer_hint_present": (case["answer_hint"] in (report["answer"] or "")) if case["answer_hint"] else None,
        "unexpected_or_invalid_call": any(r.get("fixture_blocked") or r.get("validated") is False for r in report["tool_calls"]),
        "no_business_handlers_executed": True,
    }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--env-file", type=Path, default=Path("D:/CreatorOS/.env"))
    parser.add_argument("--report", type=Path)
    parser.add_argument("--schema-snapshot", type=Path, help="Optional frozen model-visible schema JSON")
    args = parser.parse_args()
    if not args.live:
        print("No model calls without --live")
        return
    if not args.report or args.report.exists():
        raise SystemExit("A new --report path is required")
    key = (dotenv_values(args.env_file).get("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        raise SystemExit("DEEPSEEK_API_KEY_missing")
    scope = RuntimeContext(ROOT, creator_id="fixture-account", archive_only_reads=True, allowed_tools=ACCOUNT_TOOLS)
    schemas = (json.loads(args.schema_snapshot.read_text(encoding="utf-8")) if args.schema_snapshot
               else model_tool_schemas(tools, scope))
    report = {"version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "model": "deepseek-flash", "reasoning": "none", "cases": CASES, "results": [],
              "schema_snapshot": str(args.schema_snapshot) if args.schema_snapshot else None}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    for i, case in enumerate(CASES):
        variants = ("baseline", "discovery") if i % 2 == 0 else ("discovery", "baseline")
        for variant in variants:
            report["results"].append(attempt(case, variant, key, schemas, scope))
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            row = report["results"][-1]
            print(json.dumps({"case_id": row["case_id"], "variant": row["variant"], "status": row["status"],
                              "checks": row["structural_checks"], "requests": len(row["requests"])}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
