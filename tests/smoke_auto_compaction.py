from copy import deepcopy
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from creatoros.agent import loop as agent_loop
from creatoros.agent.compactor import compact_session
from creatoros.ai.types import ModelResponse, StreamEnd, TextDelta
from creatoros.terminal import Console


def summary():
    headings = (
        "Goal", "Constraints & Preferences", "Progress\n### Done\n"
        "done\n### In Progress\nnone\n### Blocked\nnone", "Key Decisions",
        "Important Facts & IDs", "Files & Artifacts", "Next Steps",
        "Unresolved Questions",
    )
    return "\n".join(f"## {heading}\nnone" for heading in headings)


class RecordingProvider:
    context_window = 4_000
    reserve_output_tokens = 500

    def __init__(self):
        self.summary_calls = 0
        self.stream_context = None

    def complete(self, context):
        self.summary_calls += 1
        return ModelResponse(summary(), [])

    def stream(self, context):
        self.stream_context = context
        yield TextDelta("done")
        yield StreamEnd("stop")


def main():
    messages = [
        {"role": "system", "content": "stable"},
        {"role": "user", "content": "old request " * 700},
        {"role": "assistant", "content": "old answer " * 700},
        {"role": "user", "content": "recent request"},
        {"role": "assistant", "content": "recent answer"},
    ]
    original = {
        "load": agent_loop.load_messages,
        "save": agent_loop.save_messages,
        "load_checkpoint": agent_loop.load_compaction_checkpoint,
        "compact": agent_loop.compact_session,
    }
    provider = RecordingProvider()
    events = []
    output = StringIO()

    with TemporaryDirectory() as directory:
        session_file = Path(directory) / "latest.json"
        agent_loop.load_messages = lambda: deepcopy(messages)
        agent_loop.save_messages = lambda value: None
        agent_loop.load_compaction_checkpoint = lambda value: None
        agent_loop.compact_session = lambda *args, **kwargs: compact_session(
            *args, **kwargs, session_file=session_file
        )
        try:
            inputs = iter(["new request", "/exit"])
            run = agent_loop.run_agent
            run(
                provider,
                console=Console(
                    input_fn=lambda prompt: next(inputs), output=output
                ),
                on_agent_event=events.append,
            )
        finally:
            agent_loop.load_messages = original["load"]
            agent_loop.save_messages = original["save"]
            agent_loop.load_compaction_checkpoint = original["load_checkpoint"]
            agent_loop.compact_session = original["compact"]

    request_messages, _ = provider.stream_context.to_request()
    assert provider.summary_calls == 1
    assert "old request" not in str(request_messages)
    assert "recent request" in str(request_messages)
    assert "new request" in str(request_messages)
    compacted = [event for event in events if event.kind == "context_compacted"]
    assert len(compacted) == 1
    assert compacted[0].data["tokens_after"] < compacted[0].data["tokens_before"]
    assert "已自动压缩" in output.getvalue()
    print("auto_compaction_smoke=passed")


if __name__ == "__main__":
    main()
