import json

import creatoros.skills.route_and_answer.runner as skill_runner
from creatoros.ai.types import ToolCall
from creatoros.tools.definitions import tool_registry
from creatoros.tools.execution import execute_tool_call
from creatoros.tools.results import ToolResult


ROUTE_PAYLOAD = {
    "source": "test",
    "hotspot_count": 2,
    "author_count": 2,
    "plans": [
        {
            "author_id": "alice",
            "display_name": "Alice",
            "hot": [{"rank": 1, "title": "平台治理", "summary": "规则变化", "score": 0.9}],
        },
        {
            "author_id": "bob",
            "display_name": "Bob",
            "hot": [{"rank": 2, "title": "教育选择", "summary": "升学问题", "score": 0.8}],
        },
    ],
}


def main():
    calls = []

    def fake_execute(tool_name, arguments, context=None):
        calls.append((tool_name, arguments))
        if tool_name == "route_hotspots":
            return ToolResult(content=json.dumps(ROUTE_PAYLOAD, ensure_ascii=False))
        if tool_name == "ask_author":
            assert arguments["author"] == "alice"
            assert arguments["query_mode"] == "grounded"
            assert arguments["writer_prompt"] == "strong_identity"
            return ToolResult(
                content="Alice 的回答",
                details={"sources": [{"title": "证据 1"}], "trace_id": "trace-test"},
            )
        raise AssertionError(f"unexpected nested tool: {tool_name}")

    previous_execute = skill_runner._execute_tool
    skill_runner._execute_tool = fake_execute
    skill_runner._snapshots.clear()
    try:
        preview = execute_tool_call(
            ToolCall("test-1", "route_and_answer", json.dumps({"mode": "preview", "limit": 2, "top_k": 1}))
        )
        preview_payload = json.loads(preview.content)
        assert not preview.is_error
        assert preview_payload["status"] == "awaiting_selection"
        snapshot_id = preview_payload["snapshot_id"]

        preview_again = execute_tool_call(
            ToolCall(
                "test-1b",
                "route_and_answer",
                json.dumps({"mode": "confirm", "snapshot_id": snapshot_id}),
            )
        )
        assert not preview_again.is_error
        assert json.loads(preview_again.content)["status"] == "awaiting_selection"

        confirm = execute_tool_call(
            ToolCall(
                "test-2",
                "route_and_answer",
                json.dumps(
                    {
                        "mode": "confirm",
                        "snapshot_id": snapshot_id,
                        "author_id": "alice",
                        "hotspot_rank": 1,
                    }
                ),
            )
        )
        answer_payload = json.loads(confirm.content)
        assert not confirm.is_error
        assert answer_payload["status"] == "answer_ready"
        assert answer_payload["answer"] == "Alice 的回答"
        assert answer_payload["trace_id"] == "trace-test"

        auto = execute_tool_call(
            ToolCall("test-3", "route_and_answer", json.dumps({"mode": "auto", "limit": 2, "top_k": 1}))
        )
        auto_payload = json.loads(auto.content)
        assert not auto.is_error
        assert auto_payload["selection"]["author_id"] == "alice"

        missing = execute_tool_call(
            ToolCall(
                "test-4",
                "route_and_answer",
                json.dumps({"mode": "confirm", "snapshot_id": "route-missing", "author_id": "alice", "hotspot_rank": 1}),
            )
        )
        assert missing.is_error and missing.error_type == "snapshot_not_found"
    finally:
        skill_runner._execute_tool = previous_execute
        skill_runner._snapshots.clear()

    assert [name for name, _ in calls] == ["route_hotspots", "ask_author", "route_hotspots", "ask_author"]
    assert "route_and_answer" in tool_registry
    assert "mode" in tool_registry["route_and_answer"].to_schema()["function"]["parameters"]["properties"]
    print("route_and_answer_skill_smoke=passed")


if __name__ == "__main__":
    main()
