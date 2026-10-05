"""SDK transport fault injection; real collector/schema, no paid model or formal data."""
import asyncio
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

from openai_codex import ApprovalMode, Sandbox
from openai_codex.generated import v2_all as sdk_types

from creatoros.integrations.codex import CodexProducer, CodexProducerError
from creatoros.integrations.topic_research import CodexTopicResearcher, ResearchReceipt
from tests.smoke_production_progress import (
    assistant_item, completed_item, notification, token_update, turn_completed,
)


def receipt(count=1):
    return json.dumps({"candidates": [{
        "title": f"borrow / lend {i}", "angle": "比较借入与借出", "rationale": "常用且易混淆",
        "sources": [{"title": "Dictionary", "url": "https://example.com/dictionary"}],
    } for i in range(count)], "note": "controlled schema fixture"}, ensure_ascii=False)


def web_item(action="search"):
    return {"id": "search-1", "type": "webSearch", "query": "common synonym groups",
            "action": {"type": action, **({"query": "common synonym groups"} if action == "search"
                                           else {"url": "https://example.com/dictionary"})}}


class ControlledTurn:
    id = "turn-1"

    def __init__(self, owner):
        self.owner = owner
        self.stream_calls = 0
        self.closed = False
        self.interrupted = False
        self.release = None

    def stream(self):
        self.stream_calls += 1

        async def events():
            try:
                yield completed_item(assistant_item("commentary", "正在搜索常用词组", "commentary"))
                if self.owner.mode in {"timeout", "cancel_running"}:
                    self.release = asyncio.Event()
                    if self.owner.mode == "cancel_running":
                        self.owner.cancel.set()
                    await self.release.wait()
                    yield turn_completed("interrupted")
                    return
                if self.owner.mode != "no_search":
                    action = "openPage" if self.owner.mode == "open_only" else "search"
                    item = web_item(action)
                    yield notification("item/started", sdk_types.ItemStartedNotification, {
                        "threadId": "thread-1", "turnId": self.id, "startedAtMs": 1, "item": item,
                    })
                    yield completed_item(item)
                if self.owner.mode == "failed":
                    yield turn_completed("failed", {"message": "controlled server turn failure"})
                    return
                final = "not JSON" if self.owner.mode == "bad_receipt" else receipt(2 if self.owner.mode == "over_count" else 1)
                yield completed_item(assistant_item("final", final, "final_answer"))
                yield token_update(17)
                yield turn_completed()
            finally:
                self.closed = True

        return events()

    async def interrupt(self):
        self.interrupted = True
        if self.release is not None:
            self.release.set()


class ControlledSdk:
    def __init__(self, mode, cancel):
        self.mode, self.cancel = mode, cancel
        self.config = None
        self.entered = False
        self.exited = False
        self.start_calls = []
        self.turn_calls = []
        self.turn_handle = None

    def build(self, config):
        self.config = config
        return self

    async def __aenter__(self):
        self.entered = True
        if self.mode in {"startup_timeout", "cancel_startup"}:
            if self.mode == "cancel_startup":
                self.cancel.set()
            await asyncio.Future()
        return self

    async def __aexit__(self, *args):
        self.exited = True

    async def thread_start(self, **kwargs):
        self.start_calls.append(kwargs)
        return self

    @property
    def id(self):
        return "thread-1"

    async def turn(self, inputs, **kwargs):
        self.turn_calls.append((inputs, kwargs))
        self.turn_handle = ControlledTurn(self)
        return self.turn_handle


def main():
    assert not issubclass(CodexTopicResearcher, CodexProducer), "research must not inherit the CLI executor"
    with TemporaryDirectory(prefix="topic-research-sdk-") as temporary:
        root = Path(temporary)
        for mode in ("success", "no_search", "open_only", "bad_receipt", "over_count", "failed",
                     "cancel_before", "cancel_startup", "cancel_running", "startup_timeout", "timeout"):
            workspace = root / mode
            workspace.mkdir()
            cancel = Event()
            if mode == "cancel_before":
                cancel.set()
            sdk = ControlledSdk(mode, cancel)
            researcher = CodexTopicResearcher(project_root=root, generated_images_root=root,
                                               timeout_seconds=0.06 if "timeout" in mode else 2)
            observed = []
            with patch.dict(os.environ, {"CREATOROS_CODEX_EXECUTABLE": ""}), \
                 patch("openai_codex.AsyncCodex", side_effect=sdk.build):
                try:
                    result = researcher.research({"series": {"name": "English"}}, 1, "常用且常考", workspace, cancel,
                                                 public_observer=observed.append)
                except Exception as error:
                    assert mode != "success", repr(error)
                    assert isinstance(error, CodexProducerError), repr(error)
                    expected = {"no_search": "research_no_search", "open_only": "research_no_search",
                                "bad_receipt": "invalid_research_receipt", "over_count": "invalid_research_receipt",
                                "failed": "codex_sdk_failed"}.get(mode)
                    if expected:
                        assert error.error_type == expected, repr(error)
                    if mode.startswith("cancel"):
                        assert isinstance(error, CodexProducerError) and error.error_type == "codex_interrupted", repr(error)
                    if "timeout" in mode:
                        assert isinstance(error, CodexProducerError) and error.error_type == "codex_timeout", repr(error)
                else:
                    assert mode == "success", f"{mode} must not become successful research"
                    assert isinstance(result.receipt, ResearchReceipt) and len(result.receipt.candidates) == 1
                    assert result.thread_id == "thread-1" and result.usage.input_tokens == 17
                    assert result.usage.output_tokens == 1
                    assert (workspace / "response.txt").read_text(encoding="utf-8") == receipt()
                    trace = (workspace / "codex_trace.jsonl").read_text(encoding="utf-8")
                    assert "thread.started" in trace and "python-codex-sdk" in trace
                    assert any(e.get("item", {}).get("type") == "web_search" for e in observed)
                    assert any(e.get("item", {}).get("text") == "正在搜索常用词组" for e in observed)
            if mode == "cancel_before":
                assert not sdk.entered
                continue
            persisted = json.loads((workspace / "production_progress.json").read_text(encoding="utf-8"))
            expected_status = "completed" if mode == "success" else "interrupted" if mode.startswith("cancel") else "failed"
            assert persisted["status"] == expected_status, (mode, persisted)
            assert sdk.entered and sdk.exited, f"SDK must close for {mode}"
            overrides = list(sdk.config.config_overrides)
            assert any("web_search" in str(x) and "live" in str(x) for x in overrides)
            assert "memories.use_memories=false" in overrides
            assert "memories.generate_memories=false" in overrides
            if sdk.start_calls:
                start = sdk.start_calls[0]
                assert len(sdk.start_calls) == 1 and str(start["cwd"]) == str(workspace)
                assert str(start["model"]) == "gpt-6-luna"
                assert start["sandbox"] == Sandbox.read_only
                assert start["approval_mode"] == ApprovalMode.deny_all
            if sdk.turn_calls:
                assert len(sdk.turn_calls) == 1
                turn_request = sdk.turn_calls[0][1]
                assert str(turn_request["effort"]) == "xhigh"
                assert turn_request["output_schema"] == ResearchReceipt.model_json_schema()
                assert sdk.turn_handle.stream_calls == 1 and sdk.turn_handle.closed
            if mode in {"timeout", "cancel_running"}:
                assert sdk.turn_handle.interrupted, "active SDK turns must receive interrupt"
            if mode in {"bad_receipt", "over_count", "no_search", "open_only"}:
                assert (workspace / "response.txt").is_file(), "retain final answer even if host validation rejects it"
    print("topic_research_sdk=passed success/typed-events/usage/search-action/schema/count/fresh-thread/cancel/startup/timeout/failure/cleanup")


if __name__ == "__main__":
    main()
