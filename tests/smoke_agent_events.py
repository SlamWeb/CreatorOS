from io import StringIO

from creatoros.agent import loop as agent_loop
from creatoros.agent.loop import run_agent
from creatoros.ai.types import StreamEnd, TextDelta, ToolCallDelta
from creatoros.terminal import Console


class FakeProvider:
    def __init__(self):
        self.calls = 0

    def stream(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            yield ToolCallDelta(0, "call-1", "get_current_time", "{}")
            yield StreamEnd("tool_calls")
        else:
            yield TextDelta("done")
            yield StreamEnd("stop")


def main():
    original_load = agent_loop.load_messages
    original_save = agent_loop.save_messages
    agent_loop.load_messages = lambda: [{"role": "system", "content": "test"}]
    agent_loop.save_messages = lambda messages: None
    try:
        inputs = iter(["what time is it", "/exit"])
        output = StringIO()
        events = []
        run_agent(
            FakeProvider(),
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
    ]
    assert "[Tool call] get_current_time" in output.getvalue()
    assert "done" in output.getvalue()
    print("agent_events_smoke=passed")


if __name__ == "__main__":
    main()
