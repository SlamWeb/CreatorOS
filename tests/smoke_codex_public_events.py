"""Local persistence/fault checks using the actual pinned SDK notification schema."""
import asyncio
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from openai_codex.generated import v2_all as sdk_types

from creatoros.integrations.codex_public_events import (
    FILENAME, PublicEventCapture, sanitize_public,
)
from creatoros.integrations.production_progress import ProgressWriter, collect_observed_turn
from creatoros.integrations.worker_protocol import record_thread
from tests.smoke_production_progress import (
    FakeTurn, assistant_item, completed_item, notification, turn_completed,
)


def records(root):
    return [json.loads(line) for line in (root / FILENAME).read_text(encoding="utf-8").splitlines()]


def delta(item_id, text, *, command=False):
    method = "item/commandExecution/outputDelta" if command else "item/agentMessage/delta"
    schema = sdk_types.CommandExecutionOutputDeltaNotification if command else sdk_types.AgentMessageDeltaNotification
    return notification(method, schema, {"threadId": "thread-1", "turnId": "turn-1",
                                        "itemId": item_id, "delta": text})


def test_sanitizer():
    long = "完整公开输出。" * 24000
    value = {"text": long, "list": list(range(120)),
             "nested": json.dumps({"items": [{"type": "reasoning", "content": "PRIVATE_REASONING"}],
                                   "api_key": "PRIVATE_JSON_KEY"}),
             "bearer": "Authorization: Bearer PRIVATE_BEARER",
             "shell": "CREATOROS_API_KEY=PRIVATE_SHELL_KEY",
             "known": "prefix " + "PRIVATE_ENV_SECRET" + " suffix",
             "image": {"type": "image", "data": "PRIVATE_IMAGE_BASE64"},
             "encoded_fields": {"b64_json": "PRIVATE_IMAGE_BODY", "image_base64": "PRIVATE_IMAGE_BODY",
                                "audioBase64": "PRIVATE_AUDIO_BODY", "data": {"public": "preserved"}},
             "encoded": "data:image/png;base64,AAAA/BBB=",
             "content": {"public": "kept", "encrypted_content": "PRIVATE_ENCRYPTED"}}
    with patch.dict(os.environ, {"CAPTURE_TEST_API_KEY": "PRIVATE_ENV_SECRET"}):
        clean, redacted, omitted = sanitize_public(value)
    saved = json.dumps(clean)
    assert "PRIVATE_" not in saved and "data:image" not in saved
    assert clean["text"] == long and clean["list"] == list(range(120))
    assert clean["encoded_fields"]["data"] == {"public": "preserved"}
    assert all(clean["encoded_fields"][key] == "[OMITTED]" for key in ("b64_json", "image_base64", "audioBase64"))
    assert json.loads(clean["nested"])["api_key"] == "[REDACTED]"
    assert redacted and omitted and value["image"]["data"] == "PRIVATE_IMAGE_BASE64"
    deeply = "tail"
    for _ in range(70):
        deeply = {"child": deeply}
    clean, _, omitted = sanitize_public(deeply)
    assert any(o["reason"] == "maximum_nesting_depth" for o in omitted)
    ordinary = {"root": "ordinary path", "other": {"data": "public JSON data"}}
    assert sanitize_public(ordinary)[0] == ordinary


def test_complete(root):
    long = "公开最终回答。" * 15000
    events = [delta("answer", "partial text must not duplicate final"),
              completed_item(assistant_item("answer", long, "final_answer")),
              completed_item({"type": "commandExecution", "id": "tool", "command": "echo visible",
                              "cwd": "C:/task", "commandActions": [], "status": "completed",
                              "aggregatedOutput": long, "exitCode": 0}),
              completed_item({"type": "mcpToolCall", "id": "mcp", "server": "local", "tool": "inspect",
                              "arguments": {"root": "C:/task", "query": long, "api_key": "PRIVATE_MCP_KEY"},
                              "status": "completed", "result": {"content": [{"type": "text", "text": long},
                                  {"type": "image", "data": "PRIVATE_BASE64", "mimeType": "image/png"}],
                                  "structuredContent": {"root": "ordinary-root", "rows": list(range(100))}}}),
              completed_item({"type": "reasoning", "id": "hidden", "content": ["PRIVATE_REASONING"],
                              "summary": ["PRIVATE_SUMMARY"]}), turn_completed()]
    turn = FakeTurn(events)
    turn.thread_id = "thread-1"
    result = asyncio.run(collect_observed_turn(turn, ProgressWriter(root, "production")))
    rows = records(root)
    assert result.final_response == long and turn.stream_calls == 1
    answers = [r for r in rows if r["item_id"] == "answer"]
    assert len(answers) == 1 and answers[0]["payload"]["item"]["text"] == long
    assert answers[0]["thread_id"] == "thread-1" and answers[0]["turn_id"] == "turn-1"
    tools = [r for r in rows if r["item_id"] == "tool"]
    assert tools[0]["payload"]["item"]["aggregated_output"] == long
    mcp = next(r for r in rows if r["item_id"] == "mcp")["payload"]["item"]
    assert mcp["arguments"]["root"] == "C:/task" and mcp["arguments"]["query"] == long
    assert mcp["result"]["structured_content"] == {"root": "ordinary-root", "rows": list(range(100))}
    assert mcp["result"]["content"][0]["text"] == long
    assert "PRIVATE_" not in (root / FILENAME).read_text(encoding="utf-8")
    hidden = next(r for r in rows if r["item_id"] == "hidden")
    assert hidden["omissions"] and hidden["payload"]["item"] == {"id": "hidden", "type": "reasoning"}
    assert not any(r["truncated"] for r in rows)
    assert "完整" not in (root / "codex_trace.jsonl").read_text(encoding="utf-8")


def test_partial(root):
    events = [delta("pending", "Bearer SECRET_"), delta("pending", "SPLIT_VALUE"),
              delta("tool", "unfinished output", command=True),
              turn_completed("failed", {"message": "server failed"})]
    try:
        asyncio.run(collect_observed_turn(FakeTurn(events), ProgressWriter(root, "research")))
        raise AssertionError("SDK failure must propagate")
    except RuntimeError as error:
        assert "server failed" in str(error)
    rows = records(root)
    partial = [r for r in rows if r["method"] == "item/partial"]
    assert len(partial) == 2 and all(r["incomplete"] and r["status"] == "failed" for r in partial)
    assert partial[0]["payload"]["item"]["text"] == "Bearer [REDACTED]"
    assert partial[1]["payload"]["item"]["aggregated_output"] == "unfinished output"
    assert "SPLIT_VALUE" not in (root / FILENAME).read_text(encoding="utf-8")
    # The same item ID in a later turn is a separate key, never a continued delta.
    second = PublicEventCapture(root, "turn-2", "research", "thread-1")
    second.observe(delta("pending", "second turn"))
    second.finish("interrupted")
    rows = records(root)
    assert next(r for r in rows if r["turn_id"] == "turn-2" and r["item_id"] == "pending")["payload"]["item"]["text"] == "second turn"


def test_cancel(root):
    class CancelTurn:
        id = "cancel-turn"
        thread_id = "thread-1"
        closed = False

        async def stream(self):
            try:
                yield delta("pending", "partial before cancellation")
                raise asyncio.CancelledError()
            finally:
                self.closed = True

    turn = CancelTurn()
    try:
        asyncio.run(collect_observed_turn(turn, ProgressWriter(root, "production")))
        raise AssertionError("cancel must propagate")
    except asyncio.CancelledError:
        pass
    assert turn.closed
    assert any(r["method"] == "item/partial" and r["status"] == "interrupted" for r in records(root))


def test_sdk_interrupted(root):
    record_thread(root, "thread-1")
    turn = FakeTurn([delta("pending", "unfinished"), turn_completed("interrupted")])
    result = asyncio.run(collect_observed_turn(turn, ProgressWriter(root, "production")))
    assert result.status.value == "interrupted", "preserve the SDK return contract"
    assert records(root)[-1]["status"] == "interrupted"
    assert any(r["method"] == "item/partial" and r["status"] == "interrupted" for r in records(root))
    receipt = json.loads((root / "worker_receipt.json").read_text(encoding="utf-8"))
    assert receipt["turns"][-1]["status"] == "interrupted"


def test_write_failure(root):
    success = [completed_item(assistant_item("answer", "FINAL", "final_answer")), turn_completed()]
    with patch.object(PublicEventCapture, "_append", side_effect=OSError("PRIVATE_DISK_DETAIL")):
        result = asyncio.run(collect_observed_turn(FakeTurn(success), ProgressWriter(root, "production")))
        assert result.final_response == "FINAL"
        try:
            asyncio.run(collect_observed_turn(FakeTurn([turn_completed("failed", {"message": "ORIGINAL SDK ERROR"})]),
                                              ProgressWriter(root, "production")))
            raise AssertionError("original failure must propagate")
        except RuntimeError as error:
            assert "ORIGINAL SDK ERROR" in str(error) and "DISK" not in str(error)
    broken = PublicEventCapture(root, "broken-pending", "production", "thread-1")
    broken.pending["broken"] = None
    broken.finish("completed")
    assert records(root)[-1]["payload"]["capture_write_failed"]


def test_tool_failure(root):
    capture = PublicEventCapture(root, "turn-1", "production", "thread-1")
    capture.observe(completed_item({"type": "commandExecution", "id": "failed-tool", "command": "exit 1",
                                   "cwd": "C:/task", "commandActions": [], "status": "completed",
                                   "aggregatedOutput": "actual error", "exitCode": 1}))
    assert records(root)[0]["method"] == "item/completed"
    assert records(root)[0]["status"] == "failed"


def test_size(root):
    capture = PublicEventCapture(root, "large-turn", "production", "thread-1")
    with patch("creatoros.integrations.codex_public_events.MAX_RECORD_BYTES", 1024):
        capture.write("item/completed", {"item": {"id": "big", "type": "agentMessage", "text": "x" * 1500}},
                      item_id="big", item_type="agentMessage")
    row = records(root)[0]
    assert row["truncated"] and row["payload"] == {}
    assert row["omissions"][0]["reason"] == "payload_too_large"
    assert row["omissions"][0]["original_bytes"] > row["omissions"][0]["limit_bytes"]


def main():
    test_sanitizer()
    with TemporaryDirectory(prefix="codex-public-capture-") as temporary:
        for test in (test_complete, test_partial, test_cancel, test_sdk_interrupted, test_write_failure, test_size, test_tool_failure):
            root = Path(temporary) / test.__name__
            root.mkdir()
            test(root)
    print("codex_public_events_smoke=passed complete_long_text tool_output redaction partial cancel single_stream best_effort size")


if __name__ == "__main__":
    main()
