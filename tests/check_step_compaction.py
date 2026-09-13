"""Safe-boundary fixtures; --live uses real DeepSeek with temporary sessions."""
import argparse
import json
import os
from io import StringIO
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from dotenv import load_dotenv

from creatoros.agent.compaction import CompactionPlan
from creatoros.agent.compactor import compact_session
from creatoros.ai.context import ModelContext, estimate_tokens
from creatoros.session.checkpoint import CompactionCheckpoint, load_compaction_checkpoint
from tests.smoke_compact_session import RecordingProvider


def step(number, size=6000):
    return [
        {"role": "assistant", "content": f"已检查资料 {number}。", "tool_calls": [
            {"id": f"call-{number}", "name": "read_file", "arguments": "{}"}]},
        {"role": "tool", "tool_call_id": f"call-{number}", "content": "资料 " + "x" * size},
    ]


def plan(messages, budget):
    return CompactionPlan.from_context(ModelContext.from_messages(messages, []),
                                       input_limit=100_000, keep_recent_tokens=budget)


def boundaries():
    user = {"role": "user", "content": "仅生成草稿，禁止发布。"}
    messages = [user, *step(0), *step(1), *step(2)]
    original = deepcopy(messages)
    budget = estimate_tokens([user, *step(2)])
    split = plan(messages, budget)
    assert split.first_retained_index == 5 and split.pinned_user_index == 0
    assert split.retained_messages == tuple(step(2))
    assert split.estimated_retained_tokens == budget
    assert messages == original
    assert not plan(messages, 100_000).can_compact
    assert not plan([user, *step(0)], 1).can_compact
    assert not plan([user], 1).can_compact

    # Multiple tool results, including out-of-order arrivals, stay atomic.
    batch = step(3)
    batch[0]["tool_calls"].append({"id": "parallel", "name": "read_file", "arguments": "{}"})
    batch.insert(1, {"role": "tool", "tool_call_id": "parallel", "content": "result"})
    split = plan([user, *step(0), *batch, *step(4)], 1)
    assert split.first_retained_index == 6
    assert split.retained_messages == tuple(step(4))
    pending = [user, *step(0), batch[0], batch[1]]
    split = plan(pending, 1)
    assert split.first_retained_index == 3
    assert split.retained_messages == tuple(pending[3:])
    malformed = [user, batch[0], batch[1], *step(4)]
    assert not plan(malformed, 1).can_compact

    # A short newest turn is retained whole, even if the previous turn was huge.
    recent = [{"role": "user", "content": "改成只预览"},
              {"role": "assistant", "content": "好的"}]
    split = plan([*messages, *recent], estimate_tokens(recent))
    assert split.pinned_user_index is None
    assert split.retained_messages == tuple(recent)


def rolling(provider, live=False):
    user = {"role": "user", "content": "整理刚才核对的校验码，最终只回复校验码和“仅草稿”，禁止发布。"}
    messages = [{"role": "system", "content": "遵守用户请求，只回复简短结论，不再调用工具。"},
                user, *step(0), *step(1), *step(2), *step(3)]
    messages[2]["content"] = "核对所得校验码：C7-ANCHOR-924。后续最终回复需要它。"
    original = deepcopy(messages)
    budget = estimate_tokens([user, *step(3)])
    with TemporaryDirectory(prefix="creatoros-step-") as directory:
        session_file = Path(directory) / "messages.json"
        session_file.write_text(json.dumps(messages, ensure_ascii=False), encoding="utf-8")
        cp = compact_session(provider, messages, [], session_file=session_file,
                             keep_recent_tokens=budget)
        assert cp and cp.first_retained_index == 8 and cp.pinned_user_index == 1
        assert load_compaction_checkpoint(messages, session_file) == cp
        assert cp.project_messages(messages).count(user) == 1
        assert cp.retained_messages == tuple(step(3))
        assert messages == original
        assert json.loads(session_file.read_text(encoding="utf-8")) == original
        assert compact_session(provider, messages, [], checkpoint=cp,
                               session_file=session_file, keep_recent_tokens=8_000) is None

        extended = [*messages, *step(4), *step(5), *step(6)]
        updated = compact_session(provider, extended, [], checkpoint=cp,
                                  session_file=session_file, keep_recent_tokens=budget)
        assert updated and updated.first_retained_index == 14
        assert updated.pinned_user_index == 1
        reloaded = load_compaction_checkpoint(extended, session_file)
        assert reloaded == updated
        projected = reloaded.project_messages(extended)
        assert projected.count(user) == 1
        assert projected[-2:] == step(6)
        assert len(updated.retained_messages) == len(extended) - updated.first_retained_index

        if live:
            response = provider.complete(ModelContext.from_messages(projected, []))
            assert "C7-ANCHOR-924" in response.content and "仅草稿" in response.content
            print("live_step_compaction=passed summaries=2 continuation=1")
            for label, usage in [("summary1", cp.usage), ("summary2", updated.usage),
                                 ("continuation", response.usage)]:
                print(label, usage.to_dict() if usage else None)
        else:
            prompt = str(provider.contexts[-1].messages)
            assert "checkpoint-1" in prompt and "call-3" in prompt
            assert "call-0" not in prompt
            # A new turn supersedes the pin once the old remaining steps compact.
            next_turn = [{"role": "user", "content": "现在停止，只预览"},
                         {"role": "assistant", "content": "收到"}]
            final = compact_session(provider, [*extended, *next_turn], [], checkpoint=updated,
                                    session_file=session_file,
                                    keep_recent_tokens=estimate_tokens(next_turn))
            assert final.first_retained_index == len(extended)
            assert final.pinned_user_index is None
            assert user not in final.project_messages([*extended, *next_turn])
            legacy = final.to_dict()
            del legacy["pinned_user_index"]
            assert CompactionCheckpoint.from_dict(legacy) == final


def automatic_loop():
    from creatoros.agent import loop
    from creatoros.ai.types import ToolCallDelta, StreamEnd, TextDelta
    from creatoros.context import RuntimeContext
    from creatoros.terminal import Console
    from creatoros.tools.results import ToolResult

    class LoopProvider(RecordingProvider):
        context_window = 6000
        reserve_output_tokens = 500

        def __init__(self):
            super().__init__()
            self.main_contexts = []

        def stream(self, context):
            self.main_contexts.append(context)
            count = len(self.main_contexts)
            if count <= 8:
                yield ToolCallDelta(0, f"loop-{count}", "read_file", "{}")
            else:
                yield TextDelta("done")
            yield StreamEnd("tool_calls" if count <= 8 else "stop")

    with TemporaryDirectory(prefix="creatoros-step-loop-") as directory:
        session_file = Path(directory) / "messages.json"
        session_file.write_text(json.dumps([{"role": "system", "content": "test"}]), encoding="utf-8")
        query = "只生成草稿，不发布，持续检查八份资料。"
        inputs = iter([query, "/exit"])
        events = []
        provider = LoopProvider()
        with patch.object(loop, "execute_tool_call", return_value=ToolResult("x" * 6000)):
            loop.run_agent(provider, session_file=session_file,
                runtime_context=RuntimeContext(Path(directory), allowed_tools=frozenset({"read_file"}),
                                               archive_only_reads=True),
                console=Console(input_fn=lambda _: next(inputs), output=StringIO()),
                on_agent_event=events.append)
        assert len(provider.main_contexts) == 9
        assert any(event.kind == "context_compacted" for event in events)
        assert not any(event.kind == "context_blocked" for event in events)
        for context in provider.main_contexts:
            assert list(context.messages).count({"role": "user", "content": query}) == 1
        ledger = json.loads(session_file.read_text(encoding="utf-8"))
        assert sum(m["role"] == "tool" for m in ledger) == 8
        cp = load_compaction_checkpoint(ledger, session_file)
        assert cp and cp.pinned_user_index == 1
        assert cp.project_messages(ledger)[-1]["content"] == "done"
        trace = [json.loads(line) for line in session_file.with_suffix(".context-trace.jsonl").read_text(
            encoding="utf-8").splitlines()]
        assert len({entry["turn_id"] for entry in trace}) == 1
    print("step_compaction_loop=passed tool_calls=8 main_calls=9")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    boundaries()
    rolling(RecordingProvider())
    automatic_loop()
    print("step_compaction_local=passed")
    if args.live:
        from creatoros.ai.deepseek import DeepSeekProvider
        load_dotenv()
        rolling(DeepSeekProvider(api_key=os.environ["DEEPSEEK_API_KEY"],
                                 timeout_seconds=90, max_retries=0), live=True)


if __name__ == "__main__":
    main()
