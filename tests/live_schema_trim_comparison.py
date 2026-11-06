"""Opt-in isolated DeepSeek A/B for mechanical JSON Schema cleanup.

No business tool is executed. The unmodified descriptions and actual registry
validation remain authoritative. Each run must use a fresh report path.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from time import monotonic

from dotenv import dotenv_values
from jsonschema import Draft202012Validator

from creatoros.ai.context import ModelContext, estimate_tokens
from creatoros.ai.deepseek import DeepSeekProvider
from creatoros.context import RuntimeContext
from creatoros.tools import tool_registry, tools
from creatoros.tools.host_contract import bind_account_arguments, model_tool_schemas
from creatoros.web.chat import ACCOUNT_INSTRUCTIONS, ACCOUNT_TOOLS


CASES = [
    ("topic_get", "把选题 test-topic-01 的完整切入点原文给我看一下。", {"manage_topics"}, {"action": "get", "topic_id": "test-topic-01"}),
    ("plan_get", "查一下计划 test-plan-01 的当前状态，先不要更改。", {"manage_plan"}, {"action": "get", "plan_id": "test-plan-01"}),
    ("skill_read", "读取 knowledge-to-carousel 的 SKILL.md 正文，我只是看看。", {"get_producer_skill"}, {"skill_id": "knowledge-to-carousel"}),
    ("failed_tasks", "列出当前账号失败的制作任务，别重试。", {"get_creator_tasks"}, {"statuses": ["failed"]}),
    ("topics_list", "列出栏目 test-series-01 下已有的选题。", {"manage_topics"}, {"action": "list", "series_id": "test-series-01"}),
    ("bgm_list", "看看我账号里现在有哪些背景音乐。", {"manage_material"}, {"action": "list"}),
    ("short_greeting", "你好，今天有什么值得开心的事？", set(), {}),
    ("no_research", "不用查资料，帮我想三个关于冬天的短篇故事点子。", set(), {}),
    ("skill_permission", "我可以编辑这个 Skill 吗？先解释权限，不要执行修改。", set(), {}),
    ("delete_ambiguous", "把刚才那个删掉。", set(), {}),
]


def trim(schemas):
    result = deepcopy(schemas)
    counts = {"null_defaults": 0, "definition_titles": 0, "nullable_anyof": 0}
    def visit(node):
        if isinstance(node, list):
            for child in node:
                visit(child)
        elif isinstance(node, dict):
            # Caller traverses schema nodes only, never business default/examples.
            if node.get("default", object()) is None:
                del node["default"]
                counts["null_defaults"] += 1
            branches = node.get("anyOf")
            if (isinstance(branches, list) and len(branches) == 2 and "type" not in node
                    and all(isinstance(branch, dict) and set(branch) == {"type"}
                            and isinstance(branch["type"], str) for branch in branches)
                    and len({branch["type"] for branch in branches}) == 2
                    and "null" in {branch["type"] for branch in branches}):
                node["type"] = [branch["type"] for branch in branches]
                del node["anyOf"]
                counts["nullable_anyof"] += 1
            for key in ("$defs", "definitions"):
                defs = node.get(key)
                if isinstance(defs, dict):
                    for name, definition in defs.items():
                        if isinstance(definition, dict) and definition.get("title") == name:
                            del definition["title"]
                            counts["definition_titles"] += 1
            for key in ("$defs", "definitions", "properties", "patternProperties", "dependentSchemas"):
                children = node.get(key)
                if isinstance(children, dict):
                    for child in children.values():
                        visit(child)
            for key in ("allOf", "anyOf", "oneOf", "prefixItems"):
                visit(node.get(key))
            for key in ("items", "additionalProperties", "contains", "not", "if", "then", "else", "propertyNames"):
                visit(node.get(key))
    for schema in result:
        visit(schema["function"]["parameters"])
        Draft202012Validator.check_schema(schema["function"]["parameters"])
    return result, counts


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--live", action="store_true")
    p.add_argument("--env-file", type=Path, default=Path("D:/CreatorOS/.env"))
    p.add_argument("--report", type=Path)
    p.add_argument("--reasoning", choices=("none", "low", "high"), default="none")
    p.add_argument("--limit", type=int, default=len(CASES))
    a = p.parse_args()
    if not a.live:
        print("No model calls without --live")
        return
    if not a.report or a.report.exists():
        raise SystemExit("A new --report path is required")
    if not 1 <= a.limit <= len(CASES):
        raise SystemExit("limit must select at least one existing case")
    key = (dotenv_values(a.env_file).get("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        raise SystemExit("DEEPSEEK_API_KEY_missing")
    scope = RuntimeContext(Path.cwd(), creator_id="synthetic-owner", archive_only_reads=True, allowed_tools=ACCOUNT_TOOLS)
    # This is the frozen pre-change account schema, not the current registry:
    # otherwise rerunning after integration would silently compare the new
    # schema against itself. It contains tool metadata only.
    baseline = Path(__file__).resolve().parents[1] / "docs/context-management/tool-schema-audit-20261107/all-schemas.json"
    original = json.loads(baseline.read_text(encoding="utf-8"))
    reduced, counts = trim(original)
    report = {"version": "schema-trim-mechanical-v1", "status": "running", "counts": counts,
              "model": "deepseek-flash", "thinking": a.reasoning,
              "schema_sha256": {name: hashlib.sha256(json.dumps(schemas, ensure_ascii=False, sort_keys=True,
                  separators=(",", ":")).encode("utf-8")).hexdigest()
                  for name, schemas in (("original", original), ("candidate", reduced))},
              "schema_estimate": {"original": estimate_tokens(original), "candidate": estimate_tokens(reduced)}, "runs": []}
    a.report.parent.mkdir(parents=True, exist_ok=True)
    def save():
        a.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    save()
    provider = DeepSeekProvider(key, model="deepseek-flash", reasoning_effort=a.reasoning, max_retries=0, timeout_seconds=90)
    try:
        for index, (case_id, query, accepted_tools, expected) in enumerate(CASES[:a.limit]):
            variants = [("original", original), ("candidate", reduced)]
            if index % 2:
                variants.reverse()
            for variant, schemas in variants:
                item = {"case_id": case_id, "variant": variant, "status": "started"}
                report["runs"].append(item); save()
                start = monotonic()
                try:
                    context = ModelContext.from_messages([{"role": "system", "content": ACCOUNT_INSTRUCTIONS},
                                                          {"role": "user", "content": query}], schemas)
                    response = provider.complete(context)
                    calls = [{"name": call.name, "arguments": call.arguments} for call in response.tool_calls]
                    item.update(status="succeeded", calls=calls, answer=response.content,
                                usage=response.usage.to_dict() if response.usage else None)
                    item["structural_check"] = len(calls) == (1 if accepted_tools else 0)
                    item["valid_arguments"] = None
                    item["expected_fields"] = None
                    if len(calls) == 1 and calls[0]["name"] in accepted_tools:
                        try:
                            arguments = json.loads(calls[0]["arguments"])
                            item["expected_fields"] = all(arguments.get(k) == v for k, v in expected.items())
                            bound = bind_account_arguments(calls[0]["name"], calls[0]["arguments"], scope)
                            tool_registry[calls[0]["name"]].parse_arguments(bound)
                            item["valid_arguments"] = True
                        except (ValueError, TypeError, KeyError):
                            item["valid_arguments"] = False
                    if accepted_tools and (len(calls) != 1 or calls[0]["name"] not in accepted_tools):
                        item["structural_check"] = False
                except Exception as exc:
                    item.update(status="failed", error_type=type(exc).__name__)
                finally:
                    item["elapsed_seconds"] = round(monotonic() - start, 3); save()
    finally:
        provider.client.close(); report["status"] = "complete"; save()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
