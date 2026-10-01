"""Focused tests for one-pass SDK turn observation and safe progress metadata."""
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from openai_codex.generated import v2_all as sdk_types
from openai_codex.models import Notification
from pydantic import ValidationError

from creatoros.integrations.production_progress import ProgressWriter, collect_observed_turn
from creatoros.integrations.skill_pair import StoryboardReceipt, VisualReceipt, join_visual


class FakeTurn:
    id = "turn-1"

    def __init__(self, events):
        self.events = events
        self.stream_calls = 0
        self.run_calls = 0

    def stream(self):
        self.stream_calls += 1

        async def consume():
            for event in self.events:
                yield event

        return consume()

    async def run(self):
        self.run_calls += 1
        raise AssertionError("stream observation must not double-consume via run()")


def notification(method, payload_type, payload):
    return Notification(method, payload_type.model_validate(payload))


def token_update(total):
    breakdown = {"inputTokens": total, "cachedInputTokens": 0,
                 "outputTokens": 1, "reasoningOutputTokens": 0, "totalTokens": total + 1}
    return notification("thread/tokenUsage/updated", sdk_types.ThreadTokenUsageUpdatedNotification, {
        "threadId": "thread-1", "turnId": "turn-1", "tokenUsage": {"last": breakdown, "total": breakdown},
    })


def completed_item(item, at=1):
    return notification("item/completed", sdk_types.ItemCompletedNotification, {
        "threadId": "thread-1", "turnId": "turn-1", "completedAtMs": at, "item": item,
    })


def assistant_item(item_id, text, phase):
    return {"type": "agentMessage", "id": item_id, "text": text, "phase": phase}


def turn_completed(status="completed", error=None):
    return notification("turn/completed", sdk_types.TurnCompletedNotification, {
        "threadId": "thread-1", "turn": {"id": "turn-1", "status": status,
                                           "error": error, "items": []},
    })


def main():
    with TemporaryDirectory(prefix="production-progress-") as temporary:
        root = Path(temporary)
        receipt_text = '{"status":"READY"}'
        tool_item = {"type": "commandExecution", "id": "tool-1", "command": "PRIVATE_ARGUMENT",
                     "cwd": "C:/private", "commandActions": [], "status": "completed"}
        events = [
            notification("item/started", sdk_types.ItemStartedNotification, {
                "threadId": "thread-1", "turnId": "turn-1", "startedAtMs": 1, "item": tool_item,
            }),
            notification("item/agentMessage/delta", sdk_types.AgentMessageDeltaNotification,
                          {"threadId": "thread-1", "turnId": "turn-1", "itemId": "assistant-final",
                           "delta": "PRIVATE_RAW_TEXT"}),
            notification("item/commandExecution/outputDelta", sdk_types.CommandExecutionOutputDeltaNotification,
                          {"threadId": "thread-1", "turnId": "turn-1", "itemId": "tool-1",
                           "delta": "PRIVATE_TOOL_OUTPUT"}),
            completed_item(assistant_item("assistant-draft", "not the receipt", "commentary"), 1),
            completed_item(tool_item, 2),
            completed_item(tool_item, 3),  # duplicate delivery must not inflate completed_tool_calls
            completed_item(assistant_item("assistant-final", receipt_text, "final_answer"), 4),
            token_update(4),
            token_update(9),  # collector retains the latest usage notification
            turn_completed(),
        ]
        turn = FakeTurn(events)
        progress = ProgressWriter(root, "production")
        result = asyncio.run(collect_observed_turn(turn, progress))

        assert turn.stream_calls == 1 and turn.run_calls == 0
        assert result.final_response == receipt_text
        assert result.usage.total.input_tokens == 9
        assert progress.state.completed_tool_calls == 1
        progress.observe("item/reasoning/summaryTextDelta", {})
        assert progress.state.activity == "thinking" and progress.state.last_event == "delta"
        progress.observe("item/commandExecution/outputDelta", {})
        assert progress.state.activity == "tool_running"
        progress.finish("completed")

        persisted = (root / "production_progress.json").read_text(encoding="utf-8")
        trace = (root / "codex_trace.jsonl").read_text(encoding="utf-8")
        for secret in ("PRIVATE_RAW_TEXT", "PRIVATE_ARGUMENT", "PRIVATE_TOOL_OUTPUT",
                       "not the receipt", receipt_text):
            assert secret not in persisted and secret not in trace
        assert "PRIVATE" not in progress.state.model_dump_json()

        failed_root = root / "failure"
        failed_root.mkdir()
        failed = ProgressWriter(failed_root, "production")
        failed_turn = FakeTurn([turn_completed("failed", {"message": "PRIVATE_ERROR"})])
        try:
            asyncio.run(collect_observed_turn(failed_turn, failed))
            raise AssertionError("failed SDK turn must propagate")
        except RuntimeError:
            failed.finish("failed")
        failure_metadata = (failed_root / "production_progress.json").read_text(encoding="utf-8")
        failure_trace = (failed_root / "codex_trace.jsonl").read_text(encoding="utf-8")
        assert "PRIVATE_ERROR" not in failure_metadata + failure_trace
        assert failed.state.status == "failed"

    storyboard = StoryboardReceipt.model_validate({
        "research_brief": "source", "causal_chain": "message → queue",
        "pages": [{"order": 1, "page_spec": "original page text"}],
    })
    visual = VisualReceipt.model_validate({
        "content_summary": "summary",
        "cards": [{"order": 1, "kind": "content", "section": None, "headline": "title",
                   "body": None, "highlights": [], "visual_brief": None,
                   "source_image_path": "C:/generated/1.png"}],
        "publish_copy": {"title": "title", "body": "body", "hashtags": []},
        "sources": [],
        "pages": [{"order": 1, "image_prompt": "prompt", "reference_assets": ["assets/character.png"]}],
    })
    joined = join_visual(storyboard, visual)
    assert joined.pages[0].page_spec == "original page text"
    old_fields = visual.model_dump()
    old_fields["pages"][0]["page_spec"] = "rewritten page"
    try:
        VisualReceipt.model_validate(old_fields)
        raise AssertionError("visual receipt must reject copied content fields")
    except ValidationError:
        pass
    print("production_progress_smoke=passed single_stream final_answer last_usage unique_tools privacy failure join")


if __name__ == "__main__":
    main()
