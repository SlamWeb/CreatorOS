"""Opt-in synthetic multi-work/version recall without forced compaction.

This probes actual loop/event construction and the real model in temporary
sessions. It never executes business tools or writes to CreatorOS data.
"""
import argparse
import json
from copy import deepcopy
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

from dotenv import dotenv_values

from creatoros.agent import loop
from creatoros.ai.deepseek import DeepSeekProvider
from creatoros.context import RuntimeContext
from creatoros.session.context_trace import read_trace
from creatoros.session.snapshot import load_messages, save_messages
from creatoros.terminal import Console
from creatoros.web.chat import ACCOUNT_INSTRUCTIONS


WORKS = [
    ("早餐观察", "日常栏目", "a1", "第一版", "一碗热粥，陪城市慢慢醒来。"),
    ("早餐观察", "成长栏目", "b1", "第一版", "赶路的人，也值得好好吃早餐。"),
    ("早餐观察", "日常栏目", "a2", "第二版", "早餐摊升起的热气，点亮清晨。"),
]
CASES = [
    ("FIRST_VERSION", "日常栏目那篇《早餐观察》最初定的开头是哪一句？"),
    ("LATEST_VERSION", "日常栏目那篇《早餐观察》后来修改后的开头是什么？"),
    ("OTHER_WORK", "成长栏目那篇《早餐观察》当时定的开头是什么？"),
    ("AMBIGUOUS_WORK", "之前那篇《早餐观察》第一版的开头是什么？"),
]


def source_messages(history_rounds=0):
    messages = [{"role": "system", "content": ACCOUNT_INSTRUCTIONS}]
    for title, series, revision, label, opening in WORKS:
        messages.append({"role": "user", "content": f"看一下{series}《{title}》{label}的资料，先讨论。"})
        bundle = {"data": {
            "target": {"topic_id": "topic-a" if series == "日常栏目" else "topic-b",
                       "series_id": series, "revision_id": revision},
            "topic": {"title": title, "brief": f"版本：{label}。开场文案：{opening}"},
        }, "images": []}
        messages.extend(loop.request_context_events(bundle, messages))
        messages.append({"role": "assistant", "content": "好的，这份资料先用于讨论。"})
    # The unrelated exchanges provide intervening history without fabricating
    # a compressed checkpoint or referring to the target openings.
    for user, answer in [
        ("我希望文字保持自然一点。", "好的，我会保持自然。"),
        ("接下来内容尽量适合手机阅读。", "明白，会考虑手机阅读。"),
        ("先不用发布，后面再看。", "好的，暂不发布。"),
    ]:
        messages.extend([{"role": "user", "content": user}, {"role": "assistant", "content": answer}])
    for number in range(1, history_rounds + 1):
        title = f"社区观察{number:02}"
        messages.append({"role": "user", "content": f"先看看《{title}》的思路，不用发布。"})
        bundle = {"data": {"target": {"topic_id": f"distractor-{number:02}", "series_id": "生活栏目"},
                           "topic": {"title": title, "brief": "写给关心社区生活的人，从一个具体生活片段切入，保持自然清楚。"}},
                  "images": []}
        messages.extend(loop.request_context_events(bundle, messages))
        messages.append({"role": "assistant", "content": f"好的，先把《{title}》作为讨论资料。"})
    return messages


def run_case(key, case_id, question, history_rounds):
    result = {"case_id": case_id, "question": question, "extra_history_rounds": history_rounds,
              "status": "failed", "requests": []}
    provider = DeepSeekProvider(key, model="deepseek-flash", reasoning_effort="low", timeout_seconds=90, max_retries=0)
    with TemporaryDirectory(prefix="creatoros-multiwork-") as directory:
        root = Path(directory)
        session = root / "messages.json"
        save_messages(deepcopy(source_messages(history_rounds)), session)
        inputs = iter([question, "/exit"])
        started = monotonic()
        try:
            loop.run_agent(provider, session_file=session, require_existing_session=True,
                           runtime_context=RuntimeContext(root, archive_only_reads=True,
                                                          allowed_tools=frozenset({"read_file"})),
                           console=Console(input_fn=lambda _: next(inputs), output=StringIO()),
                           capture_request_trace=True, max_turns=8)
            messages = load_messages(session, require_existing=True)
            result["answer"] = next((m.get("content") for m in reversed(messages)
                                     if m.get("role") == "assistant" and m.get("content") and not m.get("tool_calls")), "")
            result["tool_calls"] = [c.get("name") or (c.get("function") or {}).get("name")
                                    for m in messages for c in m.get("tool_calls", [])]
            result["status"] = "completed_needs_review"
        except Exception as exc:
            result["error_type"] = type(exc).__name__
        finally:
            result["elapsed_seconds"] = round(monotonic() - started, 3)
            rows = read_trace(session, limit=100)["items"]
            result["requests"] = [{"kind": r.get("request_kind"), "status": r.get("status"), "usage": r.get("usage")}
                                  for r in rows if r.get("event") == "finished"]
            provider.client.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--env-file", type=Path, default=Path("D:/CreatorOS/.env"))
    parser.add_argument("--report", type=Path)
    parser.add_argument("--history-rounds", type=int, default=0)
    args = parser.parse_args()
    if not args.live:
        print("No model calls without --live")
        return
    if not args.report or args.report.exists():
        raise SystemExit("A new --report path is required")
    if not 0 <= args.history_rounds <= 100:
        raise SystemExit("history-rounds must be between 0 and 100")
    key = (dotenv_values(args.env_file).get("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        raise SystemExit("DEEPSEEK_API_KEY_missing")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    results = []
    for case_id, question in CASES:
        results.append(run_case(key, case_id, question, args.history_rounds))
        args.report.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
