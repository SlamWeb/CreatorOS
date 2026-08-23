from io import StringIO

from creatoros.agent.streaming import stream_llm
from creatoros.ai.types import StreamEnd, TextDelta
from creatoros.events import AgentEvent
from creatoros.terminal import RichConsole


class FakeProvider:
    def stream(self, messages, tools):
        yield TextDelta("# CreatorOS\n\nRich output")
        yield StreamEnd("stop")


class FragmentProvider:
    def stream(self, messages, tools):
        yield TextDelta("搞定！✅ 已创建文件。\n\n")
        yield TextDelta("下一段只应追加一次。")
        yield StreamEnd("stop")


def main():
    output = StringIO()
    prompts = []

    def fake_input(prompt):
        prompts.append(prompt)
        return "hello"

    console = RichConsole(input_fn=fake_input, output=output)
    for style_name in ("creatoros.logo.cyan", "creatoros.tool", "creatoros.success"):
        assert console.rich.get_style(style_name)
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
    assert "████" in rendered
    assert "Rich output" in rendered
    assert "↳ read_file" in rendered
    assert "✓ done · ok" in rendered
    assert "[Tool call]" not in rendered
    assert "[Tool result]" not in rendered
    assert "learning build" not in rendered
    assert "┌" not in rendered
    assert "╭" not in rendered
    assert "\033[" not in rendered

    output = StringIO()
    console = RichConsole(output=output)
    console.render_event(AgentEvent("turn_start", {}))
    stream_llm(FragmentProvider(), [], [], console=console)
    streamed = output.getvalue()
    assert streamed.count("搞定！") == 1
    assert streamed.count("下一段只应追加一次") == 1
    print("rich_console_smoke=passed")


if __name__ == "__main__":
    main()
