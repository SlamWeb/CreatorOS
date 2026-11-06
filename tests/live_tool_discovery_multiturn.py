"""Opt-in cross-user-turn cache and schema reuse experiment; fixture tools only."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

from dotenv import dotenv_values
from creatoros.ai.context import ModelContext, estimate_tokens
from creatoros.ai.deepseek import DeepSeekProvider
from creatoros.context import RuntimeContext
from creatoros.tools import tool_registry, tools
from creatoros.tools.host_contract import bind_account_arguments, model_tool_schemas
from creatoros.web.chat import ACCOUNT_INSTRUCTIONS, ACCOUNT_TOOLS
from tests.live_tool_discovery_experiment import ROOT, compare_prefix, discovery_tools, fixture

QUERIES = ["请看一下选题 topic-demo-18 的制作要求是什么？", "刚才那个选题适合什么人？", "计划 plan-demo-17 现在是什么状态？"]


def run_variant(key, label, schemas, scope):
    report = {"variant": label, "turns": [], "schema_tokens_estimate": 0, "error_type": None}
    exposed = schemas if label == "baseline" else discovery_tools(schemas)
    report["schema_tokens_estimate"] = estimate_tokens(exposed)
    schema_by_name = {s["function"]["name"]: s for s in schemas}
    provider = DeepSeekProvider(key, model="deepseek-flash", reasoning_effort="none", timeout_seconds=90, max_retries=0)
    messages = [{"role": "system", "content": "测试隔离标识 " + uuid4().hex + "。\n" + ACCOUNT_INSTRUCTIONS}]
    known, previous = set(), None
    try:
        for index, question in enumerate(QUERIES):
            messages.append({"role": "user", "content": question})
            turn = {"index": index + 1, "question": question, "requests": [], "tool_calls": [], "status": "failed"}
            report["turns"].append(turn)
            for step in range(6):
                context = ModelContext.from_messages(messages, exposed)
                wire = (provider._to_openai_messages(context.to_request()[0]), context.to_request()[1])
                start = monotonic(); response = provider.complete(context)
                turn["requests"].append({"usage": response.usage.to_dict() if response.usage else None,
                    "seconds": round(monotonic() - start, 3), "prefix_from_previous": compare_prefix(previous, wire)})
                previous = deepcopy(wire); messages.append(response.to_message())
                if not response.tool_calls:
                    turn.update(status="complete_needs_review", answer=response.content)
                    break
                for call in response.tool_calls:
                    row = {"name": call.name, "arguments": call.arguments}; turn["tool_calls"].append(row)
                    try:
                        args = json.loads(call.arguments)
                        if not isinstance(args, dict):
                            raise ValueError("expected object")
                        if label == "discovery" and call.name == "discover_tools":
                            names = args.get("names")
                            if not isinstance(names, list) or not 1 <= len(names) <= 4 or any(n not in schema_by_name for n in names):
                                raise ValueError("unknown names")
                            known.update(names); output = {"tools": [schema_by_name[n] for n in names],
                                "notice": "可调用这些工具，仍需满足用户授权和账号范围。"}
                        else:
                            actual = args.get("name") if label == "discovery" and call.name == "call_discovered_tool" else call.name
                            actual_args = args.get("arguments") if label == "discovery" and call.name == "call_discovered_tool" else args
                            if label == "discovery" and (call.name != "call_discovered_tool" or actual not in known):
                                raise ValueError("tool not discovered")
                            if actual not in {"manage_topics", "manage_plan"} or not isinstance(actual_args, dict):
                                raise ValueError("outside fixture")
                            parsed = tool_registry[actual].parse_arguments(bind_account_arguments(actual, json.dumps(actual_args), scope))
                            row.update(resolved_name=actual, resolved_arguments=actual_args, validated=True)
                            output = fixture(actual, parsed)
                    except Exception as exc:
                        row.update(validated=False, error_type=type(exc).__name__)
                        output = {"fixture_error": "隔离替身不允许或参数错误，未执行。"}
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(output, ensure_ascii=False)})
            else:
                turn["status"] = "step_limit"
            if index < len(QUERIES)-1:
                sleep(5)  # Make cache persistence possible, never count as model latency.
    except Exception as exc:
        report["error_type"] = type(exc).__name__
    finally:
        provider.client.close()
    report["business_handlers_executed"] = 0
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--live",action="store_true")
    p.add_argument("--env-file",type=Path,default=Path("D:/CreatorOS/.env")); p.add_argument("--report",type=Path)
    p.add_argument("--schema-snapshot",type=Path)
    a=p.parse_args()
    if not a.live:
        print("No model calls without --live"); return
    if not a.report or a.report.exists(): raise SystemExit("A new report path is required")
    key=(dotenv_values(a.env_file).get("DEEPSEEK_API_KEY") or "").strip()
    if not key: raise SystemExit("DEEPSEEK_API_KEY_missing")
    scope=RuntimeContext(ROOT,creator_id="fixture-account",archive_only_reads=True,allowed_tools=ACCOUNT_TOOLS)
    schemas=json.loads(a.schema_snapshot.read_text(encoding="utf-8")) if a.schema_snapshot else model_tool_schemas(tools,scope)
    result={"version":1,"created_at_utc":datetime.now(timezone.utc).isoformat(),"model":"deepseek-flash",
            "reasoning":"none","schema_snapshot":str(a.schema_snapshot) if a.schema_snapshot else None,"results":[]}
    a.report.parent.mkdir(parents=True,exist_ok=True)
    for label in ("baseline","discovery"):
        result["results"].append(run_variant(key,label,schemas,scope))
        a.report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(result["results"][-1],ensure_ascii=False),flush=True)

if __name__=="__main__": main()
