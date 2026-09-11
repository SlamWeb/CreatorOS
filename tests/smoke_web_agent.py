"""Isolated HTTP/SQLite: deterministic model only for concurrency and failure injection."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch

import httpx

from creatoros.ai.types import ModelUsage, StreamEnd, TextDelta, ToolCallDelta
from creatoros.agent.loop import run_agent
from creatoros.context import RuntimeContext
from creatoros.session.snapshot import load_messages, save_messages
from creatoros.session.checkpoint import CompactionCheckpoint, save_compaction_checkpoint, load_compaction_checkpoint
from creatoros.web.app import create_app
from creatoros.web.chat import AgentChatService, STUDIO_TOOLS, WEB_INSTRUCTIONS
from creatoros.web.chat import WebConsole
from tests.smoke_auto_compaction import RecordingProvider, compaction_history
from tests.agent_studio_support import serve
from tests.studio_review_fixtures import make_fixture


class ControlledProvider:
    def __init__(self, gate):
        self.client = SimpleNamespace(close=lambda: None)
        self.gate = gate

    def stream(self, context):
        messages, tools = context.to_request()
        assert {t["function"]["name"] for t in tools} == STUDIO_TOOLS
        user = next(m["content"] for m in reversed(messages) if m["role"] == "user")
        if user == "hold":
            yield TextDelta("正在流式回答")
            assert self.gate.wait(10)
        elif user == "error":
            raise RuntimeError("SECRET-DO-NOT-EXPOSE")
        elif messages[-1]["role"] == "user":
            name = "write_file" if user == "forbidden" else "list_creators"
            yield ToolCallDelta(0, str(uuid4()), name, "{}")
            yield StreamEnd("tool_calls")
            return
        else:
            assert ("不对模型开放" if user == "forbidden" else "知识实验室") in messages[-1]["content"]
        yield TextDelta("。完成本轮。")
        yield StreamEnd("stop", ModelUsage(20, 5, 25))


def wait_idle(client, sid):
    deadline = monotonic() + 20
    while monotonic() < deadline:
        doc = client.get(f"/api/agent/sessions/{sid}").json()
        if doc["status"] != "running":
            return doc
        sleep(.05)
    raise AssertionError("chat timeout")


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        db, runs, producer, _ = make_fixture(root)
        gate = Event()
        chat_root = root / "chats"
        app = create_app(database=db, run_service=runs, chat_root=chat_root,
                         chat_provider_factory=lambda: ControlledProvider(gate))
        try:
            with serve(app) as url, httpx.Client(base_url=url, timeout=15, trust_env=False) as client:
                endpoint = "/api/agent/sessions"
                a = client.post(endpoint, json={}).json()
                b = client.post(endpoint, json={}).json()
                sid = a["id"]
                def send(text, version, request_id=None, session_id=sid):
                    return client.post(f"{endpoint}/{session_id}/turns", json={
                        "text": text, "expected_version": version, "request_id": request_id or str(uuid4())})
                rid = str(uuid4())
                assert send("hold", 0, rid).status_code == 202
                assert send("hold", 0, rid).status_code == 202  # same request is not rerun
                assert send("different", 0, rid).status_code == 409
                assert send("hold", 0, session_id=b["id"]).status_code == 409
                # A real EventSource-style observer receives partial text and can disconnect.
                with client.stream("GET", f"{endpoint}/{sid}/events") as response:
                    assert response.status_code == 200
                    for line in response.iter_lines():
                        if "正在流式回答" in line:
                            break
                assert client.get(f"{endpoint}/{sid}").json()["status"] == "running"
                gate.set()
                done = wait_idle(client, sid)
                assert done["status"] == "idle" and done["version"] == 2
                assert len([e for e in done["entries"] if e["kind"] == "user"]) == 1
                session_file = chat_root / sid / "messages.json"
                stale = load_messages(session_file)
                stale[0]["content"] = "旧宿主说明"
                save_messages(stale, session_file)
                assert send("catalog", 0).status_code == 409
                assert send("catalog", done["version"]).status_code == 202
                done = wait_idle(client, sid)
                assert WEB_INSTRUCTIONS in load_messages(session_file)[0]["content"]
                assert any(e.get("name") == "list_creators" and e["status"] == "done" for e in done["entries"])
                assert send("forbidden", done["version"]).status_code == 202
                done = wait_idle(client, sid)
                assert any(e.get("name") == "write_file" and e["status"] == "failed" for e in done["entries"])
                assert send("error", done["version"]).status_code == 202
                done = wait_idle(client, sid)
                assert done["status"] == "failed" and "SECRET" not in json.dumps(done)
                assert client.get(f"{endpoint}/{b['id']}").json()["entries"] == []
                assert send("/reset", done["version"]).status_code == 422
                assert client.post(f"{endpoint}/{sid}/turns", json={}, headers={"origin": "https://evil.example"}).status_code == 403
                assert client.get(endpoint).json()["items"]
                assert "requests" not in done and "system" not in json.dumps(done)
                assert producer.calls == 0
            # Simulate a crash after an assistant tool call was saved, before its result.
            path = chat_root / sid / "messages.json"
            messages = load_messages(path)
            cp = CompactionCheckpoint.create(summary="旧消息摘要", messages=messages,
                                              first_retained_index=1, tokens_before=200)
            save_compaction_checkpoint(cp, path)
            assert load_compaction_checkpoint(messages, path) is not None
            messages.append({"role": "assistant", "content": None, "tool_calls": [
                {"id": "unknown-call", "name": "start_content_run", "arguments": "{}"}]})
            save_messages(messages, path)
            view_path = chat_root / sid / "view.json"
            doc = json.loads(view_path.read_text(encoding="utf-8"))
            doc["status"] = "running"
            view_path.write_text(json.dumps(doc), encoding="utf-8")
            recovered = AgentChatService(chat_root)
            recovered.start()
            assert recovered.get(sid)["status"] == "interrupted"
            repaired = load_messages(path)
            assert repaired[-1]["tool_call_id"] == "unknown-call" and "未知" in repaired[-1]["content"]
            assert load_compaction_checkpoint(repaired, path) is not None
            assert not (chat_root / b["id"] / "messages.compaction.json").exists()
            with patch.dict("os.environ", {"DEEPSEEK_API_KEY": ""}):
                try:
                    recovered.submit(b["id"], str(uuid4()), "hello", 0, url)
                except Exception as e:
                    assert getattr(e, "status_code", 0) == 503
                else:
                    raise AssertionError("missing key accepted")
            assert recovered.get(b["id"])["entries"] == []
            # The actual Loop must write compaction next to the injected session, not CLI latest.json.
            compact_path = root / "compact-session" / "messages.json"
            save_messages(compaction_history(), compact_path)
            compact_provider = RecordingProvider()
            run_agent(compact_provider, console=WebConsole("new request"), session_file=compact_path,
                      runtime_context=RuntimeContext(project_root=root, allowed_tools=STUDIO_TOOLS))
            assert compact_provider.summary_calls == 1
            assert load_compaction_checkpoint(load_messages(compact_path), compact_path) is not None
            shutdown_gate = Event()
            shutdown_provider = ControlledProvider(shutdown_gate)
            shutdown_provider.client.close = shutdown_gate.set
            host = AgentChatService(root / "shutdown", lambda: shutdown_provider)
            empty = host.create()
            host.submit(empty["id"], str(uuid4()), "hold", 0, url)
            host.shutdown()
            assert host.get(empty["id"])["status"] == "interrupted"
            assert not host.thread.is_alive()
            print("web_agent_smoke=passed stream=passed isolation=passed duplicate=passed busy=passed recovery=passed tools=passed")
        finally:
            gate.set()
            db.close()


if __name__ == "__main__":
    main()
