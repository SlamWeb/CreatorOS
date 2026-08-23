from io import StringIO

from creatoros.agent.streaming import stream_llm
from creatoros.ai.types import StreamEnd, TextDelta
from creatoros.events import AgentEvent
from creatoros.terminal import RichConsole


class FakeProvider:
    def stream(self, messages, tools):
        yield TextDelta("# CreatorOS\n\nRich output")
        yield StreamEnd("stop")


def main():
    output = StringIO()
    prompts = []

    def fake_input(prompt):
        prompts.append(prompt)
        return "hello"

    console = RichConsole(input_fn=fake_input, output=output)
    console.banner()
    assert console.prompt() == "hello"
    assert prompts == ["❯ "]

    console.render_event(AgentEvent("turn_start", {}))
    response = stream_llm(FakeProvider(), [], [], console=console)
    console.render_event(AgentEvent("tool_call", {"name": "read_file"}))
    console.render_event(AgentEvent("tool_result", {"content": "ok"}))

    rendered = output.getvalue()
    assert response.content == "# CreatorOS\n\nRich output"
    assert "CreatorOS" in rendered
    assert "Rich output" in rendered
    assert "[Tool call] 正在调用" in rendered
    assert "[Tool result] 已完成" in rendered
    assert "learning build" not in rendered
    assert "┌" not in rendered
    assert "╭" not in rendered
    assert "\033[" not in rendered
    print("rich_console_smoke=passed")


if __name__ == "__main__":
    main()
