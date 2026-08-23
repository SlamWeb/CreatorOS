from io import StringIO

from creatoros.agent import loop as agent_loop
from creatoros.agent.loop import run_agent
from creatoros.ai.types import ModelUsage, StreamEnd, TextDelta, ToolCallDelta
from creatoros.tools import tools
from creatoros.terminal import Console


class FakeProvider:
    def __init__(self):
        self.calls = 0
        self.contexts = []

    def stream(self, context):
        self.contexts.append(context)
        self.calls += 1
        if self.calls == 1:
            yield ToolCallDelta(0, "call-1", "get_current_time", "{}")
            yield StreamEnd("tool_calls")
        else:
            yield TextDelta("done")
            yield StreamEnd("stop", ModelUsage(18, 2, 20))


def main():
    original_load = agent_loop.load_messages
    original_save = agent_loop.save_messages
    agent_loop.load_messages = lambda: [{"role": "system", "content": "test"}]
    agent_loop.save_messages = lambda messages: None
    try:
        inputs = iter(["what time is it", "/exit"])
        output = StringIO()
        events = []
        provider = FakeProvider()
        run_agent(
            provider,
            on_agent_event=events.append,
            console=Console(input_fn=lambda prompt: next(inputs), output=output),
            max_turns=3,
        )
    finally:
        agent_loop.load_messages = original_load
        agent_loop.save_messages = original_save

    assert [event.kind for event in events] == [
        "turn_start",
        "tool_call",
        "tool_result",
        "turn_start",
        "model_usage",
    ]
    assert "思考中" in output.getvalue()
    assert "↳ get_current_time" in output.getvalue()
    assert "✓ done ·" in output.getvalue()
    assert "done" in output.getvalue()
    assert events[-1].data["input_tokens"] == 18
    first_messages, first_tools = provider.contexts[0].to_request()
    assert first_messages[0]["role"] == "system"
    assert first_tools == tools
    print("agent_events_smoke=passed")


if __name__ == "__main__":
    main()
