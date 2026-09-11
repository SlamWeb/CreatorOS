"""Archive isolation checks and optional real summary-to-read_file verification."""
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime
from uuid import uuid4
import os

import httpx

from creatoros.agent.loop import build_model_context
from creatoros.agent.compactor import compact_session
from creatoros.context import RuntimeContext
from creatoros.session.artifacts import externalize, root_for
from creatoros.session.snapshot import save_messages, load_messages
from creatoros.tools.builtins import read_file


def fixture(secret):
    return [{"role": "system", "content": "测试"},
            {"role": "user", "content": "保存候选资料，稍后按证据回答。"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "evidence-call", "name": "get_topic_research", "arguments": "{}"}]},
            {"role": "tool", "tool_call_id": "evidence-call",
             "content": "x" * 18000 + "\n验证标记=" + secret + "\n" + "y" * 140000},
            {"role": "user", "content": "先保留资料，等待下一步。"}]


def local():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        a, b = root / "a.json", root / "b.json"
        messages = fixture("SECRET_A")
        assert build_model_context(messages, []).messages[2]["content"] == messages[3]["content"]
        projected = externalize(messages, a)
        externalize(fixture("SECRET_B"), b)
        assert "SECRET_A" not in str(projected)
        index = json.loads((root_for(a) / "index.json").read_text(encoding="utf-8"))
        target = root_for(a) / next(iter(index.values()))["path"]
        ctx = RuntimeContext(root, session_file=a, archive_only_reads=True)
        assert read_file(str(target), context=RuntimeContext(root, archive_only_reads=True)).is_error
        assert "SECRET_A" in read_file(str(target), 17990, 150, ctx, unit="chars").content
        assert target.read_text(encoding="utf-8") == messages[3]["content"]
        assert read_file(str(root_for(b) / "index.json"), context=ctx).is_error
        assert read_file("../outside.txt", context=ctx).is_error
        assert read_file(str(target), context=ctx).error_type == "page_too_large"
        assert "evidence-call" in read_file(str(root_for(a) / "index.json"), context=ctx).content
    print("archived_context=passed recent_full old_reference pagination isolation")


def live():
    from creatoros.ai.deepseek import DeepSeekProvider
    from creatoros.storage import Database, upgrade_database
    from creatoros.web import create_app
    from tests.agent_studio_support import serve
    from tests.eval_studio_tasks import send_turn, write_report, ForbiddenAction

    root = Path("tmp") / ("archived-context-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    root.mkdir(parents=True)
    url = f"sqlite:///{(root / 'eval.db').resolve().as_posix()}"
    upgrade_database(url)
    db = Database(url)
    app = create_app(database=db)
    app.state.executor.submit = ForbiddenAction().submit
    app.state.topic_research.submit = ForbiddenAction().submit
    app.state.skill_installs.submit = ForbiddenAction().submit
    provider = DeepSeekProvider(api_key=os.environ["DEEPSEEK_API_KEY"])
    report_path = root / "report.json"
    try:
        with serve(app) as base, httpx.Client(base_url=base, timeout=20) as client:
            doc = client.post("/api/agent/sessions", json={}).json()
            path = root / "eval-agent-sessions" / doc["id"] / "messages.json"
            messages = load_messages(path) + fixture(uuid4().hex)[1:]
            secret = messages[3]["content"].split("验证标记=")[1].split("\n")[0]
            save_messages(messages, path)
            checkpoint = compact_session(provider, messages, [], session_file=path, keep_recent_tokens=100)
            assert checkpoint and secret not in checkpoint.summary
            assert "index.json" in checkpoint.summary
            assert secret not in str(build_model_context(messages, [], checkpoint))
            doc = send_turn(client, doc, "请从历史候选资料找出‘验证标记’的准确值。用 read_file 查本会话的归档，不猜测；大文件可按字符分页。仅查询，不生产。")
            final = load_messages(path)
            calls = [c for m in final[len(messages):] for c in m.get("tool_calls", [])]
            answer = "\n".join(m.get("content") or "" for m in final[len(messages):] if m["role"] == "assistant" and not m.get("tool_calls"))
            report = {"passed": doc["status"] == "idle" and secret in answer and bool(calls)
                      and all(c["name"] == "read_file" for c in calls),
                      "calls": calls, "answer": answer, "summary_usage": checkpoint.usage.to_dict(),
                      "usage": [e for e in doc["entries"] if e["kind"] == "usage"],
                      "synthetic_evidence": True}
            write_report(report_path, report)
            print(f"archived_context_live={report['passed']} report={report_path}")
            assert report["passed"]
    except Exception as error:
        # Preserve a concise receipt even when the real model violates the
        # summary contract or the retrieval policy, so the badcase is inspectable.
        write_report(report_path, {"passed": False, "error": f"{type(error).__name__}: {error}", "synthetic_evidence": True})
        raise
    finally:
        provider.client.close()
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    local()
    if args.live:
        live()
