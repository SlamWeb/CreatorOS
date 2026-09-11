"""Session isolation regression; --live adds real DeepSeek over isolated HTTP."""
import argparse
import json
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

import httpx

from creatoros.agent.loop import build_model_context, run_agent
from creatoros.ai.types import StreamEnd, TextDelta, ToolCallDelta
from creatoros.context import RuntimeContext
from creatoros.session import snapshot
from creatoros.tools.builtins import read_tool_result
from creatoros.web.chat import WebConsole
from tests.agent_studio_support import serve


def history(secret):
    return [{"role": "system", "content": "Test"},
            {"role": "user", "content": "读取测试资料"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "shared-ref", "name": "list_creators", "arguments": "{}"}]},
            {"role": "tool", "tool_call_id": "shared-ref",
             "content": "x" * 18000 + "\n验证标记=" + secret + "\n" + "y" * 18000}]


class ReadProvider:
    def stream(self, context):
        if context.messages[-1]["role"] == "user":
            yield ToolCallDelta(0, "read-test", "read_tool_result", json.dumps(
                {"result_ref": "shared-ref", "offset": 17990, "limit": 200}))
            yield StreamEnd("tool_calls")
        else:
            assert "SESSION_A" in context.messages[-1]["content"]
            assert "SESSION_B" not in context.messages[-1]["content"]
            yield TextDelta("verified")
            yield StreamEnd("stop")


def local():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        a, b = root / "a.json", root / "b.json"
        snapshot.save_messages(history("SESSION_A"), a)
        snapshot.save_messages(history("SESSION_B"), b)
        other_history = snapshot.load_messages(b)
        other_history[2]["tool_calls"][0]["id"] = "only-b"
        other_history[3]["tool_call_id"] = "only-b"
        snapshot.save_messages([*snapshot.load_messages(b), *other_history[1:]], b)
        assert read_tool_result("only-b", context=RuntimeContext(root, session_file=a)).is_error
        before = a.read_bytes()
        for path, expected in [(a, "SESSION_A"), (b, "SESSION_B")]:
            ctx = RuntimeContext(root, session_file=path)
            assert expected in read_tool_result("shared-ref", 17990, 200, ctx).content
            assert read_tool_result("missing", context=ctx).is_error
        with patch.object(snapshot, "SESSION_FILE", b):
            assert "SESSION_B" in read_tool_result("shared-ref", 17990, 200).content
            wrong = RuntimeContext(root, session_file=b, allowed_tools=frozenset({"read_tool_result"}))
            assert "SESSION_A" in str(build_model_context(history("SESSION_A"), []))
            run_agent(ReadProvider(), session_file=a, runtime_context=wrong, console=WebConsole("查证"))
            assert wrong.session_file == b
        assert snapshot.load_messages(a)[3]["content"] == history("SESSION_A")[3]["content"]
        assert before != a.read_bytes()  # new conversation appended, original result retained
    print("session_result_read=passed isolation default_cli loop_binding projection")


def live():
    from creatoros.storage import Database, upgrade_database
    from creatoros.web import create_app
    from tests.eval_studio_tasks import ForbiddenAction, send_turn, write_report

    root = Path("tmp") / ("context-read-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    root.mkdir(parents=True)
    url = f"sqlite:///{(root / 'eval.db').resolve().as_posix()}"
    upgrade_database(url)
    db = Database(url)
    app = create_app(database=db)
    app.state.executor.submit = ForbiddenAction().submit
    app.state.skill_installs.submit = ForbiddenAction().submit
    app.state.topic_research.submit = ForbiddenAction().submit
    secret = uuid4().hex
    try:
        with serve(app) as base, httpx.Client(base_url=base, timeout=20) as client:
            doc = client.post("/api/agent/sessions", json={}).json()
            path = root / "eval-agent-sessions" / doc["id"] / "messages.json"
            messages = snapshot.load_messages(path)
            messages.extend(history(secret)[1:])
            snapshot.save_messages(messages, path)
            assert secret in str(build_model_context(messages, []))
            doc = send_turn(client, doc, "请用 read_tool_result 回读刚才工具资料原文并核对‘验证标记’，准确回复其值；仅查询，不生产、安装或调研。")
            saved = snapshot.load_messages(path)
            calls = [c for m in saved[len(messages):] for c in m.get("tool_calls", [])]
            answer = "\n".join(m.get("content") or "" for m in saved[len(messages):]
                               if m["role"] == "assistant" and not m.get("tool_calls"))
            report = {"passed": doc["status"] == "idle" and secret in answer and bool(calls)
                      and all(c["name"] == "read_tool_result" for c in calls),
                      "tool_calls": calls, "answer": answer,
                      "usage": [e for e in doc["entries"] if e["kind"] == "usage"],
                      "messages_path": str(path.resolve()), "synthetic_fixture": True}
            write_report(root / "report.json", report)
            print(f"live_session_result_read={report['passed']} report={root / 'report.json'}")
            assert report["passed"], report
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    local()
    if args.live:
        live()
