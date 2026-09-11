"""Deterministic budget/failure injection; no API calls or production data."""
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from types import SimpleNamespace
from time import monotonic, sleep
from uuid import uuid4

from creatoros.agent import loop
from creatoros.agent.compactor import compact_session
from creatoros.agent.compaction_summary import CompactionSummaryRequest, generate_compaction_summary
from creatoros.ai.context import ContextBudget, ModelContext
from creatoros.ai.types import ModelResponse, TextDelta, StreamEnd
from creatoros.context import RuntimeContext
from creatoros.session.snapshot import save_messages, load_messages
from creatoros.session.checkpoint import CompactionCheckpoint, save_compaction_checkpoint
from creatoros.terminal import Console
from tests.smoke_compact_session import summary


class Provider:
    context_window = 1000
    reserve_output_tokens = 100

    def __init__(self):
        self.calls = []

    def complete(self, context):
        self.calls.append(context)
        return ModelResponse(summary("test"), [])

    def stream(self, context):
        self.calls.append(context)
        yield TextDelta("ok")
        yield StreamEnd("stop")


def main():
    provider = Provider()
    request = CompactionSummaryRequest.from_messages([{"role": "user", "content": "中" * 1500}])
    try:
        generate_compaction_summary(provider, request)
        raise AssertionError("oversized summary sent")
    except ValueError:
        assert not provider.calls
    assert not ContextBudget(1000, 100, 900).is_over_limit
    assert ContextBudget(1000, 100, 901).is_over_limit
    with TemporaryDirectory() as directory:
        path = Path(directory) / "messages.json"
        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "中" * 1500}]
        save_messages(messages, path)
        events = []
        prompts = iter(["继续", "/menu"])
        loop.run_agent(provider, session_file=path,
            runtime_context=RuntimeContext(Path(directory), allowed_tools=frozenset()),
            console=Console(input_fn=lambda _: next(prompts), output=StringIO()),
            on_agent_event=events.append)
        assert not provider.calls
        assert any(e.kind == "context_blocked" for e in events)
        assert load_messages(path)[1] == messages[1]

        # Near-limit summary failure may continue; hard-limit failure may not.
        for length, expected_calls in [(790, 1), (1500, 0)]:
            provider.calls.clear()
            save_messages([messages[0], {"role": "user", "content": "中" * length}], path)
            prompts = iter(["继续", "/menu"])
            with patch.object(loop, "compact_session", side_effect=RuntimeError("SECRET")):
                output = StringIO()
                loop.run_agent(provider, session_file=path,
                    runtime_context=RuntimeContext(Path(directory), allowed_tools=frozenset()),
                    console=Console(input_fn=lambda _: next(prompts), output=output))
                assert "SECRET" not in output.getvalue()
            assert len(provider.calls) == expected_calls

        # A valid old checkpoint survives a non-shrinking replacement.
        provider.context_window, provider.reserve_output_tokens = 10000, 1000
        old = [messages[0], {"role": "user", "content": "old"},
               {"role": "assistant", "content": "ok"}]
        cp = CompactionCheckpoint.create(summary="short", messages=old,
            first_retained_index=1, tokens_before=20)
        cp_path = save_compaction_checkpoint(cp, path)
        before = cp_path.read_bytes()
        extended = [*old, {"role": "user", "content": "latest"}]
        try:
            compact_session(provider, extended, [], checkpoint=cp, session_file=path, keep_recent_tokens=10)
            raise AssertionError("non-shrinking checkpoint accepted")
        except ValueError:
            assert cp_path.read_bytes() == before
        assert provider.calls[-1].max_output_tokens == 1000
        from creatoros.web.chat import AgentChatService
        blocked = Provider()
        blocked.client = SimpleNamespace(close=lambda: None)
        host = AgentChatService(Path(directory) / "web", lambda: blocked)
        doc = host.create()
        save_messages(messages, host.root / doc["id"] / "messages.json")
        try:
            host.submit(doc["id"], str(uuid4()), "继续", doc["version"], "http://127.0.0.1:1")
            deadline = monotonic() + 5
            while host.get(doc["id"])["status"] == "running" and monotonic() < deadline:
                sleep(0.01)
            final = host.get(doc["id"])
            assert final["status"] == "failed" and "预算" in final["error"]
            assert not blocked.calls
        finally:
            host.shutdown()
    print("context_protection=passed summary_preflight hard_stop soft_fallback checkpoint_preserved")


if __name__ == "__main__":
    main()
