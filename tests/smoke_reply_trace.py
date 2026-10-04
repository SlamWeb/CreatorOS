"""Isolated reply-level trace smoke: real loopback/SQLite, deterministic model output."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import httpx

from creatoros.ai.types import StreamEnd, TextDelta, ToolCallDelta
from creatoros.agent.loop import run_agent
from creatoros.context import RuntimeContext
from creatoros.session.snapshot import load_messages, save_messages
from creatoros.terminal import Console
from creatoros.web.app import create_app
from tests.agent_studio_support import serve
from tests.studio_review_fixtures import make_fixture


REUSED_TOOL_CALL_ID = "same-call-id-across-turns"
ENV_SECRET = "reply-trace-smoke-known-secret"


class TraceProvider:
    """Deterministic Provider that records what the real Loop hands it."""

    def __init__(self):
        self.client = SimpleNamespace(close=lambda: None)
        self.contexts = []
        self.calls = 0

    @staticmethod
    def _prompt(messages):
        return next(message["content"] for message in reversed(messages)
                    if message.get("role") == "user" and
                    isinstance(message.get("content"), str) and
                    not message["content"].startswith("[宿主提供的当前账号目录"))

    def stream(self, context):
        messages, tools = context.to_request()
        self.contexts.append((messages, tools))
        self.calls += 1
        prompt = self._prompt(messages)
        allowed = {item["function"]["name"] for item in tools}
        if prompt in {"turn one", "turn two", "error tool"} and self.calls == 1:
            name = "unknown_tool" if prompt == "error tool" else "list_creators"
            arguments = '{"api_key":"' + ENV_SECRET + '","authorization":"Bearer hidden","cookie":"hidden-cookie"}' if prompt == "error tool" else (
                '{"offset":0,"limit":20}' if prompt == "turn one" else
                '{"offset":1,"limit":2}')
            if name in allowed:
                arguments = '{"offset":0,"limit":20}' if prompt == "turn one" else '{"offset":1,"limit":2}'
            yield ToolCallDelta(0, REUSED_TOOL_CALL_ID, name, arguments)
            yield StreamEnd("tool_calls")
            return
        if prompt == "partial":
            yield TextDelta("可见但没有 StreamEnd 的部分回答")
            return
        if prompt == "length limited":
            yield TextDelta("受输出上限截断的回答")
            yield StreamEnd("length")
            return
        if prompt == "failed partial":
            yield TextDelta("失败前已流出的部分回答")
            raise RuntimeError("private upstream failure")
        if prompt == "account check":
            assert "知识实验室" in json.dumps(messages, ensure_ascii=False)
            assert "[测试] 账号 B" not in json.dumps(messages, ensure_ascii=False)
            assert "list_creators" in allowed
        elif self.calls > 1 and prompt in {"turn one", "turn two", "error tool"}:
            # The actual tool result must be present before the follow-up model call.
            assert messages[-1]["role"] == "tool"
        yield TextDelta("完成：" + prompt)
        yield StreamEnd("stop")


def wait_idle(client, endpoint, session_id):
    deadline = monotonic() + 20
    while monotonic() < deadline:
        doc = client.get(f"{endpoint}/{session_id}").json()
        if doc["status"] != "running":
            return doc
        sleep(0.03)
    raise AssertionError("isolated Agent request timed out")


def send(client, endpoint, session_id, text, version, request_id=None):
    return client.post(f"{endpoint}/{session_id}/turns", json={
        "text": text, "expected_version": version,
        "request_id": request_id or str(uuid4()),
    })


def main():
    with TemporaryDirectory() as directory, patch.dict(os.environ, {"TRACE_API_KEY": ENV_SECRET}):
        root = Path(directory)
        database, run_service, producer, _ = make_fixture(root)
        from creatoros.storage import ContentRepository, CreatorPlatform
        content = ContentRepository(database)
        content.create_creator(creator_id="trace-b", display_name="[测试] 账号 B",
                               platform=CreatorPlatform.XIAOHONGSHU)
        # Use disjoint fixture names to prove the scoped context excludes account B.

        chat_root = root / "isolated-chats"
        providers = []

        def provider_factory():
            provider = TraceProvider()
            providers.append(provider)
            return provider

        app = create_app(database=database, run_service=run_service, chat_root=chat_root,
                         chat_provider_factory=provider_factory)
        endpoint = "/api/agent/sessions"
        try:
            with serve(app) as url, httpx.Client(base_url=url, timeout=15, trust_env=False) as client:
                sid = client.post(endpoint, json={}).json()["id"]
                creator_sid = client.post(endpoint, json={"creator_id": "review-lab"}).json()["id"]

                turn_one = str(uuid4())
                assert send(client, endpoint, sid, "turn one", 0, turn_one).status_code == 202
                first = wait_idle(client, endpoint, sid)
                assert first["status"] == "idle"
                assert any(entry.get("complete") is True and entry.get("terminal") is True
                           for entry in first["entries"] if entry["kind"] == "assistant")

                turn_two = str(uuid4())
                assert send(client, endpoint, sid, "turn two", first["version"], turn_two).status_code == 202
                second = wait_idle(client, endpoint, sid)
                assert second["status"] == "idle"

                turn_one_url = f"{endpoint}/{sid}/turn-trace/{turn_one}"
                turn_two_url = f"{endpoint}/{sid}/turn-trace/{turn_two}"
                index_one_response = client.get(turn_one_url)
                index_two_response = client.get(turn_two_url)
                assert index_one_response.status_code == index_two_response.status_code == 200
                assert index_one_response.headers.get("cache-control") == "no-store"
                assert index_two_response.headers.get("cache-control") == "no-store"
                index_one, index_two = index_one_response.json(), index_two_response.json()
                assert index_one["available"] and index_two["available"]
                assert [row["turn_id"] for row in index_one["requests"]] == [turn_one] * 2
                assert [row["turn_id"] for row in index_two["requests"]] == [turn_two] * 2
                assert [row["request_kind"] for row in index_one["requests"]] == ["main", "main"]

                rid_one, rid_one_followup = [row["request_id"] for row in index_one["requests"]]
                rid_two, rid_two_followup = [row["request_id"] for row in index_two["requests"]]
                snapshot_url = lambda turn, rid: f"{endpoint}/{sid}/turn-trace/{turn}/requests/{rid}"
                snap_one = client.get(snapshot_url(turn_one, rid_one)).json()
                snap_one_followup = client.get(snapshot_url(turn_one, rid_one_followup)).json()
                snap_two = client.get(snapshot_url(turn_two, rid_two)).json()
                assert snap_one["response"]["tool_calls"][0]["id"] == REUSED_TOOL_CALL_ID
                assert snap_two["response"]["tool_calls"][0]["id"] == REUSED_TOOL_CALL_ID
                assert snap_one["response"]["tool_calls"][0]["arguments"] == '{"offset":0,"limit":20}'
                assert snap_two["response"]["tool_calls"][0]["arguments"] == '{"offset":1,"limit":2}'
                assert snap_one["tool_results"][0]["tool_call_id"] == REUSED_TOOL_CALL_ID
                assert snap_two["tool_results"][0]["tool_call_id"] == REUSED_TOOL_CALL_ID
                assert snap_one["tool_results"][0]["content"] == snap_one_followup["context"]["messages"][-1]["content"]
                assert snap_two["tool_results"][0]["content"] == client.get(snapshot_url(turn_two, rid_two_followup)).json()["context"]["messages"][-1]["content"]

                # Tool errors are stored as model-visible tool results with call identity.
                error_turn = str(uuid4())
                assert send(client, endpoint, sid, "error tool", second["version"], error_turn).status_code == 202
                after_error = wait_idle(client, endpoint, sid)
                error_index = client.get(f"{endpoint}/{sid}/turn-trace/{error_turn}").json()
                error_request = next(row for row in error_index["requests"] if row["tool_calls"])
                error_snapshot_response = client.get(snapshot_url(error_turn, error_request["request_id"]))
                assert error_snapshot_response.status_code == 200
                error_snapshot = error_snapshot_response.json()
                assert error_snapshot["tool_results"][0]["is_error"] is True
                assert error_snapshot["tool_results"][0]["error_type"] == "unknown_tool"
                assert error_snapshot["redacted"] is True
                saved_error_snapshot = chat_root / sid / "messages.request-trace" / f"{error_request['request_id']}.json"
                serialized_error = saved_error_snapshot.read_text(encoding="utf-8")
                assert ENV_SECRET not in serialized_error and "hidden-cookie" not in serialized_error
                assert "headers" not in serialized_error.lower()
                assert ENV_SECRET not in error_snapshot_response.text
                assert "authorization\":\"Bearer hidden" not in error_snapshot_response.text
                assert "requests" not in json.dumps(after_error)

                # The scoped request gets a freshly built A-only tree.
                account_turn = str(uuid4())
                assert send(client, endpoint, creator_sid, "account check", 0, account_turn).status_code == 202
                account_done = wait_idle(client, endpoint, creator_sid)
                assert account_done["status"] == "idle"
                account_index = client.get(f"{endpoint}/{creator_sid}/turn-trace/{account_turn}").json()
                account_snapshot = client.get(
                    f"{endpoint}/{creator_sid}/turn-trace/{account_turn}/requests/{account_index['requests'][0]['request_id']}"
                ).json()
                assert "知识实验室" in json.dumps(account_snapshot["context"]["messages"], ensure_ascii=False)
                assert "[测试] 账号 B" not in json.dumps(account_snapshot["context"]["messages"], ensure_ascii=False)

                # Streaming without StreamEnd is partial; provider failure preserves a failed trace.
                partial_turn = str(uuid4())
                assert send(client, endpoint, sid, "partial", after_error["version"], partial_turn).status_code == 202
                partial_done = wait_idle(client, endpoint, sid)
                partial_assistant = [entry for entry in partial_done["entries"] if entry.get("turn_id") == partial_turn]
                assert partial_assistant and partial_assistant[-1]["complete"] is False
                assert partial_assistant[-1]["terminal"] is True
                partial_index = client.get(f"{endpoint}/{sid}/turn-trace/{partial_turn}").json()
                assert partial_index["requests"][-1]["status"] == "interrupted"

                length_turn = str(uuid4())
                assert send(client, endpoint, sid, "length limited", partial_done["version"], length_turn).status_code == 202
                limited_done = wait_idle(client, endpoint, sid)
                limited_index = client.get(f"{endpoint}/{sid}/turn-trace/{length_turn}").json()
                assert limited_index["requests"][-1]["finish_reason"] == "length"
                assert not any(entry.get("complete") is True for entry in limited_done["entries"]
                               if entry.get("turn_id") == length_turn)

                failed_turn = str(uuid4())
                assert send(client, endpoint, sid, "failed partial", limited_done["version"], failed_turn).status_code == 202
                failed_done = wait_idle(client, endpoint, sid)
                failed_index_response = client.get(f"{endpoint}/{sid}/turn-trace/{failed_turn}")
                assert failed_index_response.status_code == 200
                failed_index = failed_index_response.json()
                assert failed_index["requests"][-1]["status"] == "failed"
                failed_id = failed_index["requests"][-1]["request_id"]
                failed_snapshot = client.get(snapshot_url(failed_turn, failed_id)).json()
                assert failed_snapshot["response"] is None
                assert not any(entry.get("complete") is True for entry in failed_done["entries"]
                               if entry.get("turn_id") == failed_turn)

                # Session/turn/request ownership is enforced by the real HTTP routes.
                other_session = client.post(endpoint, json={}).json()["id"]
                assert client.get(f"{endpoint}/{other_session}/turn-trace/{turn_one}").status_code == 404
                assert client.get(f"{endpoint}/{sid}/turn-trace/{str(uuid4())}").status_code == 404
                assert client.get(snapshot_url(turn_one, rid_two)).status_code == 404
                assert client.get(snapshot_url(turn_one, "../view.json")).status_code == 404
                missing_id = rid_one_followup
                (chat_root / sid / "messages.request-trace" / f"{missing_id}.json").unlink()
                assert client.get(snapshot_url(turn_one, missing_id)).status_code == 404
                malformed_id = rid_two_followup
                malformed_path = chat_root / sid / "messages.request-trace" / f"{malformed_id}.json"
                malformed_original = malformed_path.read_bytes()
                malformed_path.write_text("{broken json", encoding="utf-8")
                assert client.get(snapshot_url(turn_two, malformed_id)).status_code == 404
                for corrupt in ([], {**json.loads(malformed_original), "context": {"messages": None, "tools": []}}):
                    malformed_path.write_text(json.dumps(corrupt), encoding="utf-8")
                    assert client.get(snapshot_url(turn_two, malformed_id)).status_code == 404
                malformed_path.write_bytes(malformed_original)

                # Old ledger entries stay unavailable; they never gain a fabricated turn ID.
                legacy_turn = str(uuid4())
                view_path = chat_root / sid / "view.json"
                view = json.loads(view_path.read_text(encoding="utf-8"))
                view["requests"].append({"id": legacy_turn, "text": "legacy history"})
                view["entries"].append({"kind": "assistant", "text": "old answer"})
                view_path.write_text(json.dumps(view, ensure_ascii=False), encoding="utf-8")
                legacy_index = client.get(f"{endpoint}/{sid}/turn-trace/{legacy_turn}").json()
                assert legacy_index == {"turn_id": legacy_turn, "status": "finished", "requests": [], "available": False}

                # Capture exact bytes, change current history, restart the host, and verify the old snapshot.
                saved_one = (chat_root / sid / "messages.request-trace" / f"{rid_one}.json").read_bytes()
                messages_path = chat_root / sid / "messages.json"
                messages = load_messages(messages_path)
                messages[0]["content"] = "[测试] 以后变更的 system 指令"
                save_messages(messages, messages_path)
                restarted_app = create_app(database=database, run_service=run_service, chat_root=chat_root,
                                           chat_provider_factory=provider_factory)
            with serve(restarted_app) as restarted_url, httpx.Client(base_url=restarted_url, timeout=15, trust_env=False) as client:
                replay = client.get(f"{endpoint}/{sid}/turn-trace/{turn_one}/requests/{rid_one}")
                assert replay.status_code == 200
                assert replay.json() == json.loads(saved_one)
                assert "[测试] 以后变更的 system 指令" not in replay.text

            # CLI/ordinary Loop defaults do not create request payload snapshots.
            cli_path = root / "cli-session" / "messages.json"
            inputs = iter(["CLI prompt", "/exit"])
            run_agent(TraceProvider(), console=Console(input_fn=lambda _prompt: next(inputs)),
                      session_file=cli_path, runtime_context=RuntimeContext(project_root=root, allowed_tools=()))
            assert not cli_path.with_suffix(".request-trace").exists()
            # Opt-in compaction snapshots share the same host turn, but retain
            # their own real summary input/output rather than the main context.
            from tests.smoke_auto_compaction import RecordingProvider, compaction_history
            from creatoros.session.context_trace import read_trace
            from creatoros.session.request_trace import RequestSnapshots
            from creatoros.web.chat import STUDIO_TOOLS
            compact_path = root / "compacted" / "messages.json"
            save_messages(compaction_history(), compact_path)
            compact_inputs = iter(["continue", "/exit"])
            compact_turn = str(uuid4())
            run_agent(RecordingProvider(),
                      console=Console(input_fn=lambda _prompt: next(compact_inputs)),
                      session_file=compact_path, user_request_id=compact_turn,
                      capture_request_trace=True,
                      runtime_context=RuntimeContext(project_root=root, allowed_tools=STUDIO_TOOLS))
            compact_rows = [row for row in read_trace(compact_path, limit=100)["items"]
                            if row["event"] == "finished"]
            assert [row["request_kind"] for row in compact_rows] == ["compaction", "main"]
            assert {row["turn_id"] for row in compact_rows} == {compact_turn}
            compact_snapshot = RequestSnapshots(compact_path).read(compact_rows[0]["request_id"])
            assert compact_snapshot["context"]["tools"] == []
            assert compact_snapshot["context"]["max_output_tokens"] > 0
            assert "compact conversation checkpoints" in compact_snapshot["context"]["messages"][0]["content"]
            assert "## Goal" in compact_snapshot["response"]["content"]
            assert producer.calls == 0
            print("reply_trace_smoke=passed http=passed turn_isolation=passed tool_results=passed errors=passed partials=passed redaction=passed restart=passed legacy=passed cli_default=passed")
        finally:
            database.close()


if __name__ == "__main__":
    main()
