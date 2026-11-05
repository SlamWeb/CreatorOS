"""Small paid DeepSeek probe of checkpoint recall, with synthetic local data only.

Run explicitly: python -m tests.live_history_compaction_recall --live
No business services, production accounts, image generation or publication.
"""
from copy import deepcopy
import argparse
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from dotenv import dotenv_values

from creatoros.agent.compactor import compact_session
from creatoros.agent.loop import run_agent
from creatoros.ai.context import estimate_tokens
from creatoros.ai.deepseek import DeepSeekProvider
from creatoros.context import RuntimeContext
from creatoros.session.snapshot import save_messages
from creatoros.terminal import Console


def history(marker):
    # Marker deliberately lives only in the middle of a tool result, not in
    # the user request, assistant text, or tool name.
    body = json.dumps({"action": "get", "topic": {"id": "synthetic-topic", "title": "测试选题",
        "brief": "背景说明" * 800 + marker + "补充材料" * 800}}, ensure_ascii=False)
    return [
        {"role": "system", "content": "你是只读助理。需要时可用 read_file 查当前会话归档；结果未知时不要重试。"},
        {"role": "user", "content": "请处理栏目选题，只允许草稿，禁止发布；如果提交结果未知，先核对，不能再次提交。"},
        {"role": "assistant", "content": "我会先查询生产结果。", "tool_calls": [
            {"id": "result-unknown", "name": "manage_plan", "arguments": "{}"}]},
        {"role": "tool", "tool_call_id": "result-unknown", "content":
            '[tool_error type=plan_write_outcome_unknown]\n{"status":"unknown","message":"远端可能已经提交，结果未知，不得重新提交"}'},
        {"role": "assistant", "content": "收到回执，后续先核对。"},
        {"role": "user", "content": "读取选题完整制作要求，以便后续核对。"},
        {"role": "assistant", "content": "查询要求。", "tool_calls": [
            {"id": "brief-result", "name": "manage_topics", "arguments": "{}"}]},
        {"role": "tool", "tool_call_id": "brief-result", "content": body},
        {"role": "assistant", "content": "制作要求已获取。"},
        {"role": "user", "content": "先等一下，之后我会继续询问。"},
        {"role": "assistant", "content": "好的。"},
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Opt in to paid DeepSeek calls")
    if not parser.parse_args().live:
        print("No model calls: pass --live to run the isolated paid probe.")
        return
    key = (dotenv_values().get("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        raise SystemExit("DEEPSEEK_API_KEY_missing")
    marker = "核验暗号-" + uuid4().hex[:8]
    provider = DeepSeekProvider(key, model="deepseek-flash", reasoning_effort="low",
                                timeout_seconds=90, max_retries=0)
    source = history(marker)
    query = "请根据之前的工具记录回答：提交结果是否已确定、能否再次提交？原制作要求中的核验暗号是什么？是否允许发布？"
    output = {}
    with TemporaryDirectory(prefix="creatoros-history-recall-") as directory:
        for name in ("uncompacted", "compacted"):
            root = Path(directory) / name
            root.mkdir()
            path = root / "messages.json"
            save_messages(deepcopy(source), path)
            cp = None
            if name == "compacted":
                cp = compact_session(provider, source, [], session_file=path,
                                     keep_recent_tokens=estimate_tokens(source[-2:]))
                assert cp is not None
                output["summary_usage"] = cp.usage.to_dict() if cp.usage else None
                output["summary_unknown"] = "unknown" in cp.summary.lower() or "未知" in cp.summary
                output["summary_marker"] = marker in cp.summary
                output["estimated_original"] = estimate_tokens(source)
                output["estimated_compacted"] = estimate_tokens(cp.project_messages(source))
            inputs = iter([query, "/exit"])
            events, stream = [], StringIO()
            run_agent(provider, session_file=path, require_existing_session=True,
                      runtime_context=RuntimeContext(root, allowed_tools=frozenset({"read_file"}),
                                                     archive_only_reads=True),
                      console=Console(input_fn=lambda _: next(inputs), output=stream),
                      on_agent_event=events.append, max_turns=8)
            ledger = json.loads(path.read_text(encoding="utf-8"))
            result = next((m.get("content") for m in reversed(ledger)
                           if m.get("role") == "assistant" and m.get("content") and not m.get("tool_calls")), "")
            output[name] = {"reply": result, "marker_found": marker in result,
                "tool_calls": [event.data.get("name") for event in events if event.kind == "tool_call"],
                "usages": [event.data for event in events if event.kind == "model_usage"]}
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
