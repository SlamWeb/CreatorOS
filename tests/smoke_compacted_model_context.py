from copy import deepcopy
from io import StringIO

from creatoros.agent import loop as agent_loop
from creatoros.agent.loop import build_model_context, run_agent
from creatoros.ai.types import StreamEnd, TextDelta
from creatoros.session.checkpoint import CompactionCheckpoint
from creatoros.terminal import Console


class CapturingProvider:
    def stream(self, context):
        self.context = context
        yield TextDelta("done")
        yield StreamEnd("stop")


def main():
    checkpoint_source = [
        {"role": "system", "content": "stable instructions"},
        {"role": "user", "content": "old request"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "recent request"},
        {"role": "assistant", "content": "recent answer"},
    ]
    checkpoint = CompactionCheckpoint.create(
        summary="## Goal\ncontinue CreatorOS",
        messages=checkpoint_source,
        first_retained_index=3,
        tokens_before=900_000,
    )
    raw_messages = [
        *deepcopy(checkpoint_source),
        {"role": "user", "content": "new request"},
    ]
    tools = [{"type": "function", "function": {"name": "read_file"}}]

    context = build_model_context(raw_messages, tools, checkpoint)
    request_messages, request_tools = context.to_request()

    assert request_messages[0] == checkpoint_source[0]
    assert request_messages[1]["role"] == "user"
    assert "continue CreatorOS" in request_messages[1]["content"]
    assert request_messages[2:4] == checkpoint_source[3:]
    assert request_messages[4] == raw_messages[-1]
    assert "old request" not in str(request_messages)
    assert request_tools == tools
    assert raw_messages == [*checkpoint_source, {"role": "user", "content": "new request"}]

    original_load = agent_loop.load_messages
    original_save = agent_loop.save_messages
    original_checkpoint_load = agent_loop.load_compaction_checkpoint
    agent_loop.load_messages = lambda: deepcopy(raw_messages)
    agent_loop.save_messages = lambda messages: None
    agent_loop.load_compaction_checkpoint = lambda messages: checkpoint
    provider = CapturingProvider()
    try:
        inputs = iter(["follow up", "/exit"])
        run_agent(
            provider,
            console=Console(
                input_fn=lambda prompt: next(inputs),
                output=StringIO(),
            ),
        )
    finally:
        agent_loop.load_messages = original_load
        agent_loop.save_messages = original_save
        agent_loop.load_compaction_checkpoint = original_checkpoint_load

    loop_messages, _ = provider.context.to_request()
    assert "continue CreatorOS" in loop_messages[1]["content"]
    assert "old request" not in str(loop_messages)
    assert loop_messages[-1]["content"] == "follow up"

    print("compacted_model_context_smoke=passed")


if __name__ == "__main__":
    main()
