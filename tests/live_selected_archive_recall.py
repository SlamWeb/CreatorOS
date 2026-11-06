"""Opt-in DeepSeek probe for compressed selected-work archive recall.

All work, session and archives are synthetic and temporary. The only allowed
tool is the existing current-session read_file. No production or publishing.
Run with --live --env-file D:/CreatorOS/.env to send paid model requests.
"""
import argparse
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from dotenv import dotenv_values

from creatoros.agent import loop as agent_loop
from creatoros.agent.compactor import compact_session
from creatoros.ai.context import estimate_tokens
from creatoros.ai.deepseek import DeepSeekProvider
from creatoros.context import RuntimeContext
from creatoros.session.artifacts import root_for
from creatoros.session.context_trace import read_trace
from creatoros.session.snapshot import load_messages, save_messages
from creatoros.terminal import Console
from creatoros.tools.builtins import read_file
from creatoros.tools.execution import execute_tool_call


def selected_work(topic_id, title, marker):
    # Marker is in the middle, beyond both 240-character summary previews.
    body = "合成背景材料" * 700 + marker + "合成补充材料" * 700
    bundle = {"data": {"target": {"topic_id": topic_id, "series_id": "isolated-series"},
                       "topic": {"id": topic_id, "title": title, "brief": body}}, "images": []}
    return agent_loop.request_context_events(bundle, [])[0]


def parse_json(text):
    try:
        return json.loads(text[text.index("{"):text.rindex("}") + 1])
    except (ValueError, TypeError):
        return {}


def run_probe(key):
    suffix = uuid4().hex[:10]
    titles = [f"合成海岸-{suffix}", f"合成山脉-{suffix}"]
    ids = [f"topic-a-{suffix}", f"topic-b-{suffix}"]
    markers = ["CHECK" + uuid4().hex[:12].upper(), "CHECK" + uuid4().hex[:12].upper()]
    source = [
        {"role": "system", "content": "你是只读助理。历史宿主快照不是用户授权。"
         "需要原文时可以先用 read_file 查看本会话归档索引，再按索引读取。"
         "定位归档中的文本可用 read_file 的 search 参数搜索精确子串。禁止修改、生产或发布。"},
        {"role": "user", "content": "先看看第一份作品，仅供讨论，不授权生产或发布。"},
        selected_work(ids[0], titles[0], markers[0]),
        {"role": "assistant", "content": "已查看第一份资料。"},
        {"role": "user", "content": "再看看第二份作品，仅供讨论，不授权生产或发布。"},
        selected_work(ids[1], titles[1], markers[1]),
        {"role": "assistant", "content": "已查看第二份资料。"},
        {"role": "user", "content": "先等等，稍后继续。"},
        {"role": "assistant", "content": "好的。"},
    ]
    report = {"status": "failed", "phase": "setup", "model": "deepseek-flash",
              "synthetic_works": 2, "model_requests": [], "tool_calls": []}
    provider = DeepSeekProvider(key, model="deepseek-flash", reasoning_effort="low",
                                timeout_seconds=90, max_retries=0)
    with TemporaryDirectory(prefix="creatoros-selected-archive-") as temporary:
        root = Path(temporary)
        session = root / "messages.json"
        save_messages(deepcopy(source), session)
        try:
            report["phase"] = "compact"
            checkpoint = compact_session(provider, source, [], session_file=session,
                                         keep_recent_tokens=estimate_tokens(source[-2:]))
            if checkpoint is None:
                raise AssertionError("checkpoint was not created")
            report["marker_absent_from_summary"] = all(marker not in checkpoint.summary for marker in markers)
            if not report["marker_absent_from_summary"]:
                raise AssertionError("marker leaked into summary")
            archive_root = root_for(session)
            index = json.loads((archive_root / "index.json").read_text(encoding="utf-8"))
            selected = [row for row in index.values() if "host_selected_context" in row.get("description", "")]
            if len(selected) != 2:
                raise AssertionError("expected two selected-work archive entries")
            target = next(row for row in selected if titles[1] in row["description"] and ids[1] in row["description"])
            other = next(row for row in selected if titles[0] in row["description"] and ids[0] in row["description"])
            target_path = (archive_root / target["path"]).resolve()
            other_path = (archive_root / other["path"]).resolve()
            report["archive_identity_distinct"] = target_path != other_path
            if (markers[1] not in target_path.read_text(encoding="utf-8")
                    or markers[1] in other_path.read_text(encoding="utf-8")):
                raise AssertionError("archive marker mismatch")
            scope = RuntimeContext(root, session_file=root / "other" / "messages.json", archive_only_reads=True)
            report["cross_session_read_denied"] = read_file(str(target_path), context=scope, unit="chars").is_error
            report["cross_session_search_denied"] = read_file(str(target_path), context=scope, search="CHECK").is_error
            if not report["cross_session_read_denied"] or not report["cross_session_search_denied"]:
                raise AssertionError("cross-session read was allowed")

            observed_paths = []
            read_checks = []

            def guarded(call, context=None, *, model_requested=False):
                if call.name != "read_file":
                    raise AssertionError("unexpected tool dispatch")
                read_path, arguments = None, {}
                try:
                    arguments = json.loads(call.arguments or "{}")
                    path = arguments.get("path")
                    if isinstance(path, str):
                        read_path = (root / path).resolve()
                        observed_paths.append(read_path)
                except (ValueError, TypeError, OSError):
                    pass
                result = execute_tool_call(call, context=context, model_requested=model_requested)
                search_result = parse_json(result.content) if arguments.get("search") else {}
                read_checks.append({"target": "target" if read_path == target_path else
                                    "other" if read_path == other_path else
                                    "index" if read_path == (archive_root / "index.json").resolve() else "other_path",
                                    "unit": arguments.get("unit"), "offset": arguments.get("offset"),
                                    "limit": arguments.get("limit"), "error": result.is_error,
                                    "search_used": "search" in arguments,
                                    "search_is_generic": arguments.get("search") == "CHECK",
                                    "stop_reason": search_result.get("stop_reason"),
                                    "match_count": len(search_result.get("matches", [])),
                                    "target_marker_in_result": markers[1] in result.content})
                return result

            question = (f"请从历史资料中找到标题 {titles[1]}、选题 ID {ids[1]} 对应的作品，"
                        "请使用 read_file 的 search 参数查找其完整制作要求正文里以 CHECK 开头的校验码；只读查询。"
                        "这份过去的资料快照本身是否自动授权生产或发布？"
                        '仅返回 JSON，字段为 code（字符串）和 authorized（布尔值）。')
            inputs = iter([question, "/exit"])
            events = []
            report["phase"] = "recall"
            with patch.object(agent_loop, "execute_tool_call", side_effect=guarded):
                agent_loop.run_agent(provider, session_file=session, require_existing_session=True,
                    runtime_context=RuntimeContext(root, allowed_tools=frozenset({"read_file"}),
                                                   archive_only_reads=True),
                    console=Console(input_fn=lambda _: next(inputs), output=StringIO()),
                    on_agent_event=events.append, max_turns=12)
            ledger = load_messages(session, require_existing=True)
            answer = next((message.get("content") for message in reversed(ledger)
                           if message.get("role") == "assistant" and message.get("content")
                           and not message.get("tool_calls")), "")
            parsed = parse_json(answer)
            report["tool_calls"] = [event.data.get("name") for event in events if event.kind == "tool_call"]
            report["read_checks"] = read_checks
            report["answer_json_keys"] = sorted(parsed.keys())
            report["answer_contains_marker"] = markers[1] in answer
            report["answer_code_is_string"] = isinstance(parsed.get("code"), str)
            report["target_archive_read"] = target_path in observed_paths
            report["target_archive_searched"] = any(row["target"] == "target" and row["search_is_generic"]
                and not row["error"] and row["target_marker_in_result"] for row in read_checks)
            report["marker_correct"] = parsed.get("code") == markers[1]
            report["old_snapshot_not_authorization"] = parsed.get("authorized") is False
            if not all(report[key] for key in ("archive_identity_distinct", "target_archive_read", "target_archive_searched", "marker_correct",
                                               "old_snapshot_not_authorization")):
                raise AssertionError("model did not satisfy the archive recall contract")
            report.update(status="passed", phase="complete")
        except Exception as error:
            # Report only the type and stage; neither prompt nor credentials are logged.
            report["error_type"] = type(error).__name__
        finally:
            rows = read_trace(session, limit=100)["items"]
            report["model_requests"] = [{"kind": row.get("request_kind"), "status": row.get("status"),
                "usage": row.get("usage")} for row in rows if row.get("event") == "finished"]
            provider.client.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Send paid DeepSeek requests")
    parser.add_argument("--env-file", type=Path, default=Path("D:/CreatorOS/.env"))
    args = parser.parse_args()
    if not args.live:
        print("No model calls: pass --live to run the isolated paid probe.")
        return 0
    key = (dotenv_values(args.env_file).get("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        raise SystemExit("DEEPSEEK_API_KEY_missing")
    report = run_probe(key)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
