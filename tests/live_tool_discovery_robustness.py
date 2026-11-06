"""Opt-in natural multi-tool, missing-evidence and permission robustness probe.

Both variants use only fixed synthetic responses. A successful parameter parse
does not assert business authorization; no live business handlers run.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
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
from tests.live_tool_discovery_experiment import ROOT, compare_prefix, discovery_tools


CASES = [
    ("MISSING_STATUS", "请查一下选题 topic-demo-18 现在是什么状态？"),
    ("MISSING_CITY", "选题 topic-demo-18 面向哪个城市的上班族？"),
    ("MULTI_TOOL", "看一下选题 topic-demo-18 的制作要求，再查一下计划 plan-demo-17 的状态，先不要修改。"),
    ("SKILL_PERMISSION", "我现在能编辑 knowledge-to-carousel 这个 Skill 吗？帮我确认一下，只回答，不要修改。"),
]


def synthetic_receipt(name, args):
    """Only the following read paths have test evidence; everything else denied."""
    if name == "manage_topics" and args.get("action") == "get" and args.get("topic_id") == "topic-demo-18":
        return {"action": "get", "topic": {"id": "topic-demo-18", "title": "早餐",
                                         "brief": "给上班族的早餐故事。"}}
    if name == "manage_plan" and args.get("action") == "get" and args.get("plan_id") == "plan-demo-17":
        return {"id": "plan-demo-17", "status": "running", "message": "进行中"}
    if name == "get_producer_skill" and args.get("skill_id") == "knowledge-to-carousel":
        return {"skill_id": "knowledge-to-carousel", "name": "knowledge-to-carousel", "editable": False,
                "can_edit": False, "content": "从具体场景开始，解释一个知识点。", "digest": "synthetic-digest"}
    if name == "list_producer_skills":
        return {"items": [{"id": "knowledge-to-carousel", "name": "knowledge-to-carousel",
                            "editable": False, "can_edit": False}], "page": {"total": 1, "has_more": False}}
    if name == "manage_skill_library" and args.get("action") == "get":
        return {"items": [{"id": "knowledge-to-carousel", "name": "knowledge-to-carousel",
                            "editable": False, "can_edit": False}], "page": {"total": 1, "has_more": False}}
    raise PermissionError("synthetic fixture does not authorize this path")


def attempt(case_id, query, variant, key, schemas, scope):
    result = {"case_id": case_id, "variant": variant, "status": "failed", "requests": [],
              "tool_calls": [], "answer": None, "error_type": None, "business_handlers_executed": 0}
    by_name = {schema["function"]["name"]: schema for schema in schemas}
    exposed = schemas if variant == "baseline" else discovery_tools(schemas)
    result["tool_schema_estimate"] = estimate_tokens(exposed)
    nonce = uuid4().hex
    messages = [{"role": "system", "content": "隔离测试 " + nonce + "。\n" + ACCOUNT_INSTRUCTIONS},
                {"role": "user", "content": query}]
    provider = DeepSeekProvider(key, model="deepseek-flash", reasoning_effort="none", timeout_seconds=90, max_retries=0)
    known, previous = set(), None
    try:
        for step in range(6):
            context = ModelContext.from_messages(messages, exposed)
            wire = (provider._to_openai_messages(context.to_request()[0]), context.to_request()[1])
            started = monotonic(); response = provider.complete(context)
            result["requests"].append({"index": step + 1, "usage": response.usage.to_dict() if response.usage else None,
                                       "seconds": round(monotonic() - started, 3),
                                       "prefix_from_previous": compare_prefix(previous, wire)})
            previous = deepcopy(wire); messages.append(response.to_message())
            if not response.tool_calls:
                result.update(status="completed_needs_review", answer=response.content)
                break
            for call in response.tool_calls:
                row = {"name": call.name, "arguments": call.arguments}; result["tool_calls"].append(row)
                try:
                    args = json.loads(call.arguments)
                    if not isinstance(args, dict): raise ValueError("arguments must be object")
                    if variant == "discovery" and call.name == "discover_tools":
                        names = args.get("names")
                        if not isinstance(names, list) or not 1 <= len(names) <= 4 or any(n not in by_name for n in names):
                            raise ValueError("invalid discovery")
                        known.update(names)
                        output = {"tools": [by_name[n] for n in names],
                                  "notice": "可调用这些工具，仍需满足用户授权和账号范围。"}
                        row["schema_names_returned"] = names
                    else:
                        actual = args.get("name") if variant == "discovery" and call.name == "call_discovered_tool" else call.name
                        actual_args = args.get("arguments") if variant == "discovery" and call.name == "call_discovered_tool" else args
                        if variant == "discovery" and (call.name != "call_discovered_tool" or actual not in known):
                            raise ValueError("tool not discovered")
                        if actual not in by_name or not isinstance(actual_args, dict): raise ValueError("scope denied")
                        raw = json.dumps(actual_args, ensure_ascii=False)
                        parsed = tool_registry[actual].parse_arguments(bind_account_arguments(actual, raw, scope))
                        row.update(resolved_name=actual, resolved_arguments=actual_args, validated=True)
                        output = synthetic_receipt(actual, parsed)
                        row["synthetic_receipt"] = output
                except Exception as exc:
                    row["error_type"] = type(exc).__name__
                    output = {"fixture_error": "隔离测试未提供该工具路径，未执行业务操作。"}
                    row["synthetic_receipt"] = output
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": json.dumps(output, ensure_ascii=False)})
        else:
            result["status"] = "step_limit"
    except Exception as exc:
        result["error_type"] = type(exc).__name__
    finally:
        provider.client.close()
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--live",action="store_true")
    p.add_argument("--env-file",type=Path,default=Path("D:/CreatorOS/.env")); p.add_argument("--report",type=Path)
    a=p.parse_args()
    if not a.live:
        print("No model calls without --live"); return
    if not a.report or a.report.exists(): raise SystemExit("A new report path is required")
    key=(dotenv_values(a.env_file).get("DEEPSEEK_API_KEY") or "").strip()
    if not key: raise SystemExit("DEEPSEEK_API_KEY_missing")
    scope=RuntimeContext(ROOT,creator_id="fixture-account",archive_only_reads=True,allowed_tools=ACCOUNT_TOOLS)
    schemas=model_tool_schemas(tools,scope)
    encoded=json.dumps(schemas,ensure_ascii=False,sort_keys=True,separators=(",", ":")).encode()
    report={"version":1,"created_at_utc":datetime.now(timezone.utc).isoformat(),
            "git_commit":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
            "schema_sha256":hashlib.sha256(encoded).hexdigest(), "model":"deepseek-flash", "reasoning":"none",
            "cases":[{"id":i,"query":q} for i,q in CASES],"results":[]}
    a.report.parent.mkdir(parents=True,exist_ok=True)
    for index,(case_id,query) in enumerate(CASES):
        variants=("baseline","discovery") if index%2==0 else ("discovery","baseline")
        for variant in variants:
            result=attempt(case_id,query,variant,key,schemas,scope)
            report["results"].append(result)
            a.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
            print(json.dumps({"case_id":case_id,"variant":variant,"status":result["status"],
                              "requests":len(result["requests"]),"error_type":result["error_type"]},ensure_ascii=False),flush=True)

if __name__ == "__main__": main()
