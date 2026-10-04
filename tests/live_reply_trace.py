"""Two low-cost, read-only DeepSeek checks for reply-level request snapshots.

Runs only against a temporary SQLite database and temporary chat directory.
The output contains request counts, usage, and tool names, never prompts, keys,
snapshot contents, or provider exceptions.
"""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx

from creatoros.ai import DeepSeekProvider
from creatoros.session.context_trace import read_trace
from creatoros.storage import ContentRepository, ContentRun
from creatoros.web import chat as chat_module
from creatoros.web.app import create_app
from tests.agent_studio_support import serve
from tests.smoke_account_sessions import wait_live
from tests.studio_review_fixtures import make_fixture


READ_ONLY_TOOLS = frozenset({
    "list_creators", "list_creator_series", "list_series_topics",
    "list_producer_skills", "get_producer_skill",
})


class RecordingDeepSeekProvider(DeepSeekProvider):
    """Record the exact ModelContext passed to the real DeepSeek stream call."""

    def __init__(self, api_key):
        super().__init__(api_key=api_key, timeout_seconds=60, max_retries=0)
        self.sent_contexts = []

    def stream(self, context):
        messages, tools = context.to_request()
        self.sent_contexts.append({
            "messages": messages,
            "tools": tools,
            "max_output_tokens": context.max_output_tokens,
        })
        yield from super().stream(context)


def _snapshot_contexts(client, base, session_id, turn_id, provider):
    index_response = client.get(f"{base}/{session_id}/turn-trace/{turn_id}")
    assert index_response.status_code == 200, index_response.status_code
    index = index_response.json()
    rows = index["requests"]
    snapshots = []
    for row in rows:
        detail = client.get(
            f"{base}/{session_id}/turn-trace/{turn_id}/requests/{row['request_id']}"
        )
        assert detail.status_code == 200, detail.status_code
        snapshot = detail.json()
        assert snapshot["turn_id"] == turn_id
        snapshots.append(snapshot)

    assert len(snapshots) == len(provider.sent_contexts), (
        len(snapshots), len(provider.sent_contexts)
    )
    for saved, sent in zip(snapshots, provider.sent_contexts):
        assert saved["context"] == sent
    tool_names = [
        call["name"]
        for snapshot in snapshots
        for call in (snapshot.get("response") or {}).get("tool_calls", [])
    ]
    usage = [row.get("usage") for row in rows]
    return rows, snapshots, tool_names, usage


def main():
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        print("live_reply_trace=unavailable reason=DEEPSEEK_API_KEY_missing")
        return

    providers = []

    def provider_factory():
        provider = RecordingDeepSeekProvider(api_key)
        providers.append(provider)
        return provider

    with TemporaryDirectory(prefix="reply-trace-live-") as temporary:
        root = Path(temporary)
        database, run_service, producer, _ = make_fixture(root)
        ContentRepository(database).create_creator(
            creator_id="other-account", display_name="OTHER_ACCOUNT_SENTINEL"
        )
        chat_root = root / "chats"
        # app.state exposes `chat` (not `agent_chat`). Narrow the Loop's allowed
        # tools for this live check before either request is accepted.
        from unittest.mock import patch
        with patch.object(chat_module, "ACCOUNT_TOOLS", READ_ONLY_TOOLS):
            app = create_app(database=database, run_service=run_service,
                             chat_root=chat_root, chat_provider_factory=provider_factory)
            try:
                assert app.state.chat.root == chat_root
                with serve(app) as url, httpx.Client(base_url=url, timeout=210, trust_env=False) as client:
                    base = "/api/agent/sessions"
                    session = client.post(base, json={"creator_id": "review-lab"}).json()
                    prompts = [
                        "请直接根据当前账号对话已注入的宿主目录，告诉我绑定账号名称、栏目名、受众和绑定 Skill 的 name 与 description。只读回答，不调用工具，也不要读取 Skill 正文。",
                        "我明确要求查看刚才绑定的 Skill 写法：请调用 get_producer_skill，只读取 knowledge-to-carousel 的 SKILL.md offset=0、limit=200，并概述这 200 个字符。不要调用其他工具，不要调研、生产、安装或修改任何数据。",
                    ]
                    turns = []
                    for prompt in prompts:
                        turn_id = str(uuid4())
                        response = client.post(f"{base}/{session['id']}/turns", json={
                            "request_id": turn_id,
                            "text": prompt,
                            "expected_version": session["version"],
                        })
                        assert response.status_code == 202, response.status_code
                        session = wait_live(client, session["id"])
                        assert session["status"] == "idle", session.get("error")
                        turns.append(turn_id)

                    assert len(providers) == 2
                    first_rows, first_snapshots, first_tools, first_usage = _snapshot_contexts(
                        client, base, session["id"], turns[0], providers[0]
                    )
                    second_rows, second_snapshots, second_tools, second_usage = _snapshot_contexts(
                        client, base, session["id"], turns[1], providers[1]
                    )
                    assert first_rows and second_rows
                    assert not first_tools, first_tools
                    assert "get_producer_skill" in second_tools, second_tools
                    assert set(first_tools + second_tools) <= READ_ONLY_TOOLS

                    all_snapshots = first_snapshots + second_snapshots
                    snapshot_text = json.dumps(all_snapshots, ensure_ascii=False)
                    assert "OTHER_ACCOUNT_SENTINEL" not in snapshot_text
                    assert "知识实验室" in snapshot_text
                    assert "把 Agent 讲明白" in snapshot_text
                    # The second request context includes a bounded Skill page only
                    # after the explicit read tool has returned its result.
                    assert any(
                        result["name"] == "get_producer_skill"
                        and result["content"]
                        for snapshot in second_snapshots
                        for result in snapshot["tool_results"]
                    )

                    ledger_path = chat_root / session["id"] / "messages.json"
                    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
                    calls = [call for message in ledger for call in message.get("tool_calls", [])]
                    assert all(call["name"] in READ_ONLY_TOOLS for call in calls), [
                        call["name"] for call in calls
                    ]
                    assert "get_producer_skill" in [call["name"] for call in calls]
                    trace_rows = [
                        row for row in read_trace(ledger_path, limit=200)["items"]
                        if row.get("event") == "finished" and row.get("request_kind") == "main"
                    ]
                    assert len(trace_rows) == len(first_rows) + len(second_rows)
                    assert all(row.get("usage") is not None for row in trace_rows)
                    assert producer.calls == 0
                    with database.session() as db_session:
                        assert db_session.query(ContentRun).count() == 0

                    print(json.dumps({
                        "real_deepseek": True,
                        "queries": 2,
                        "main_requests_per_query": [len(first_rows), len(second_rows)],
                        "usage_per_query": [first_usage, second_usage],
                        "tool_names_per_query": [first_tools, second_tools],
                        "snapshot_contexts_exact": True,
                        "producer_calls": producer.calls,
                    }, ensure_ascii=False))
            except Exception as error:
                # Avoid provider exception strings: SDK errors can contain request details.
                raise SystemExit(
                    f"live_reply_trace=failed exception_type={type(error).__name__}"
                ) from None
            finally:
                database.close()
    print("live_reply_trace=passed isolated_db=passed read_only=passed")

if __name__ == "__main__":
    main()
