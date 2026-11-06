"""Opt-in isolated A/B for the real agent loop and synthetic tool receipts.

This runs DeepSeek but intercepts every canonical business tool dispatch; there
is no business DB, service, production or publication. Real business routes
are validated separately by the frozen tool-calling suite.
"""
import argparse
from copy import deepcopy
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from time import monotonic
from unittest.mock import patch
from uuid import uuid4

from dotenv import dotenv_values

from creatoros.agent import loop
from creatoros.agent.tool_protocol import select_tool_protocol
from creatoros.context import RuntimeContext
from creatoros.session.context_trace import read_trace
from creatoros.session.snapshot import load_messages, save_messages
from creatoros.terminal import Console
from creatoros.tools import tool_registry
from creatoros.tools.host_contract import bind_account_arguments
from creatoros.tools.results import ToolResult
from creatoros.web.chat import ACCOUNT_INSTRUCTIONS, ACCOUNT_TOOLS
from tests.live_context_multiturn_compare import RecordingProvider, snapshot


CASES = (
    ("G01_HELLO", ("你好，我们一起整理创作思路吧。",)),
    ("G02_TOPIC", ("请看一下选题 topic-demo-18 的完整切入点。",)),
    ("G03_MULTI", ("看看选题 topic-demo-18 的切入点，再看看计划 plan-demo-17 的状态。",)),
    ("G04_MISSING", ("选题 topic-demo-18 现在是什么状态？",)),
    ("G05_SCOPE", ("查一下另一个账号的选题 topic-other-18 的内容。",)),
    ("G06_HISTORY", ("请看一下选题 topic-demo-18 的切入点。", "刚才那份选题是写给谁的？")),
    ("G07_UNKNOWN", ("刚才提交的制作结果还不确定，现在该怎么处理？",)),
    ("G08_EDIT_UNKNOWN", ("请把选题 topic-demo-18 的标题改为午间早餐。", "刚才修改成功了吗？")),
)


def source_hashes():
    root = Path(__file__).resolve().parents[1]
    files = ("creatoros/agent/loop.py", "creatoros/agent/tool_protocol.py",
             "creatoros/tools/discovery_gateway.py", "creatoros/tools/action_catalog.py")
    individual = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in files}
    all_sources = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in sorted((root / "creatoros").rglob("*.py"))}
    individual["all_creatoros_python_files"] = hashlib.sha256(
        json.dumps(all_sources, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    individual["all_creatoros_python_count"] = len(all_sources)
    return individual


def check_prefix(previous, current):
    if previous is None:
        return None
    return {"same_schema": previous[1] == current[1],
            "messages_append_only": previous[0] == current[0][:len(previous[0])]}


def initial_messages(case_id, nonce):
    messages = [{"role": "system", "content": "隔离测试 " + nonce + "。\n" + ACCOUNT_INSTRUCTIONS}]
    if case_id == "G07_UNKNOWN":
        messages.extend([
            {"role": "user", "content": "给这个选题提交一次制作。"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "unknown-1",
                "name": "start_content_run", "arguments": '{"action":"start","topic_ids":["topic-demo-18"]}'}]},
            {"role": "tool", "tool_call_id": "unknown-1", "content":
             '[tool_error type=studio_outcome_unknown]\n{"status":"unknown","message":"提交结果未知，请核对原请求"}'},
            {"role": "assistant", "content": "提交结果未知，不能确认是否已受理。"},
        ])
    return messages


def run_one(key, case_id, queries, protocol, fastpath):
    result = {"id": case_id, "protocol": protocol, "fastpath": fastpath,
              "status": "failed", "turns": [],
              "canonical_calls": [], "fixture_only": True}
    provider = RecordingProvider(key)
    with TemporaryDirectory(prefix="creatoros-gateway-acceptance-") as directory:
        root = Path(directory)
        session = root / "messages.json"
        seed = initial_messages(case_id, uuid4().hex)
        if case_id == "G07_UNKNOWN" and protocol == "discovery":
            # This synthetic prior history models an already-pinned discovery
            # session; otherwise the product intentionally treats existing
            # conversations as legacy regardless of the environment switch.
            prefix = seed[:1]
            with patch.dict(os.environ, {"CREATOROS_TOOL_PROTOCOL": protocol,
                                         "CREATOROS_TOOL_FASTPATH": "1" if fastpath else "0"}):
                select_tool_protocol(prefix, RuntimeContext(root, creator_id="synthetic-owner",
                                                            session_file=session, archive_only_reads=True,
                                                            allowed_tools=ACCOUNT_TOOLS))
            seed = prefix + seed[1:]
        save_messages(seed, session)
        state = snapshot(1)
        state["creator"]["id"] = "synthetic-owner"

        def execute(call, context=None, *, model_requested=False):
            row = {"name": call.name, "arguments": call.arguments, "status": "blocked"}
            result["canonical_calls"].append(row)
            try:
                args = json.loads(call.arguments or "{}")
                bound = bind_account_arguments(call.name, call.arguments, context)
                tool_registry[call.name].parse_arguments(bound)
                row["parameters_validated"] = True
            except (ValueError, TypeError, KeyError):
                row["parameters_validated"] = False
                return ToolResult("参数 JSON 无效。", True, "invalid_arguments")
            if call.name == "manage_topics" and args.get("action") == "get":
                if args.get("topic_id") == "topic-other-18":
                    row["status"] = "scope_denied_fixture"
                    return ToolResult("当前账号无权读取该选题。", True, "scope_denied")
                if args.get("topic_id") == "topic-demo-18":
                    row["status"] = "fixture_ok"
                    # Deliberately no status: the answer must not invent one.
                    return ToolResult(json.dumps({"action": "get", "topic": {"id": "topic-demo-18",
                        "series_id": "target-series", "title": "早餐", "brief": "给上班族的早餐故事。",
                        "edit_digest": "a" * 64}}, ensure_ascii=False))
            if case_id == "G08_EDIT_UNKNOWN" and call.name == "manage_topics" and args.get("action") == "update":
                row["status"] = "synthetic_unknown_write"
                return ToolResult(json.dumps({"status": "unknown", "message": "修改结果未知，请核对原请求。"},
                                             ensure_ascii=False), True, "studio_outcome_unknown")
            if call.name == "manage_plan" and args.get("action") == "get" and args.get("plan_id") == "plan-demo-17":
                row["status"] = "fixture_ok"
                return ToolResult(json.dumps({"id": "plan-demo-17", "status": "running", "message": "进行中"}, ensure_ascii=False))
            return ToolResult("本隔离夹具不执行该操作。", True, "fixture_blocked")

        try:
            with patch.dict(os.environ, {"CREATOROS_TOOL_PROTOCOL": protocol,
                                         "CREATOROS_TOOL_FASTPATH": "1" if fastpath else "0"}), patch.object(loop, "execute_tool_call", side_effect=execute):
                previous = None
                for question in queries:
                    before_contexts, before_calls = len(provider.contexts), len(result["canonical_calls"])
                    events = []
                    inputs = iter((question, "/exit"))
                    started = monotonic()
                    loop.run_agent(provider, session_file=session, require_existing_session=True,
                        runtime_context=RuntimeContext(root, creator_id="synthetic-owner", archive_only_reads=True,
                                                       allowed_tools=ACCOUNT_TOOLS),
                        context_factory=lambda: deepcopy(state),
                        console=Console(input_fn=lambda _: next(inputs), output=StringIO()),
                        on_agent_event=events.append, capture_request_trace=True, max_turns=10)
                    ledger = load_messages(session, require_existing=True)
                    answer = next((m.get("content") for m in reversed(ledger)
                                   if m.get("role") == "assistant" and m.get("content") and not m.get("tool_calls")), "")
                    contexts = provider.contexts[before_contexts:]
                    if not contexts:
                        raise RuntimeError("No model request was recorded")
                    schema_names = [t.get("function", {}).get("name") for t in contexts[0][1]]
                    has_gateway = "discover_tools" in schema_names and "call_discovered_tool" in schema_names
                    has_direct = "read_topic" in schema_names and "list_topics" in schema_names
                    if has_gateway != (protocol == "discovery") or (has_gateway and has_direct != fastpath):
                        raise RuntimeError("Requested protocol/fastpath was not present in the actual model request")
                    prefixes = []
                    for context in contexts:
                        prefixes.append(check_prefix(previous, context)); previous = context
                    result["turns"].append({"question": question, "answer": answer,
                        "wall_seconds": round(monotonic() - started, 3),
                        "model_requests": len(contexts), "prefix_from_previous": prefixes,
                        "wire_schema_names": schema_names,
                        "wire_tool_results": [[{"tool_call_id": m.get("tool_call_id"),
                            "content": m.get("content", "")[:10000] if isinstance(m.get("content"), str) else None,
                            "truncated": isinstance(m.get("content"), str) and len(m["content"]) > 10000}
                            for m in context[0] if m.get("role") == "tool"] for context in contexts],
                        "canonical_calls": result["canonical_calls"][before_calls:],
                        "events": [{"kind": e.kind, "data": e.data} for e in events
                                   if e.kind in ("tool_call", "model_usage")]})
            result["status"] = "completed_needs_review"
        except Exception as exc:
            result["error_type"] = type(exc).__name__
            result["error_message"] = str(exc)[:300]
        finally:
            result["requests"] = [{"kind": row.get("request_kind"), "status": row.get("status"), "usage": row.get("usage")}
                for row in read_trace(session, limit=100)["items"] if row.get("event") == "finished"]
            provider.client.close()
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--live", action="store_true")
    p.add_argument("--env-file", type=Path, default=Path("D:/CreatorOS/.env"))
    p.add_argument("--report", type=Path)
    args = p.parse_args()
    if not args.live:
        print("No model calls without --live"); return
    if not args.report or args.report.exists():
        raise SystemExit("A new --report path is required")
    key = (dotenv_values(args.env_file).get("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        raise SystemExit("DEEPSEEK_API_KEY_missing")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = {"version": "gateway-natural-v1", "status": "running", "model": "deepseek-flash",
              "reasoning": "none", "source_hashes_start": source_hashes(), "results": [],
              "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True,
                                                   cwd=Path(__file__).resolve().parents[1]).strip(),
              "test_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "actual_billing": None, "actual_billing_status": "not_observed",
              "fixture_policy": "Synthetic tool receipts; canonical business handlers intercepted; no production/publication."}
    for index, (case_id, queries) in enumerate(CASES):
        modes = (("legacy", False), ("discovery", False), ("discovery", True))
        if index % 2:
            modes = tuple(reversed(modes))
        for protocol, fastpath in modes:
            row = run_one(key, case_id, queries, protocol, fastpath)
            report["results"].append(row)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"id": case_id, "protocol": protocol, "fastpath": fastpath, "status": row["status"],
                              "requests": len(row["requests"])}, ensure_ascii=False), flush=True)
    report["status"] = "complete"
    report["source_hashes_end"] = source_hashes()
    report["sources_unchanged_during_run"] = report["source_hashes_start"] == report["source_hashes_end"]
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
