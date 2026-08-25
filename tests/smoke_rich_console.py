from io import StringIO

from creatoros.agent.streaming import stream_llm
from creatoros.ai.context import ModelContext
from creatoros.ai.types import StreamEnd, TextDelta
from creatoros.events import AgentEvent
from creatoros.terminal import RichConsole


class FakeProvider:
    def stream(self, context):
        yield TextDelta("# CreatorOS\n\nRich output")
        yield StreamEnd("stop")


class FragmentProvider:
    def stream(self, context):
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
    empty_context = ModelContext.from_messages([], [])
    response = stream_llm(FakeProvider(), empty_context, console=console)
    console.render_event(AgentEvent("tool_call", {"name": "read_file"}))
    assert console._status is not None
    assert console._active_tool_name == "read_file"
    console.render_event(
        AgentEvent(
            "tool_result",
            {"name": "read_file", "is_error": False, "error_type": None},
        )
    )
    assert console._status is None
    assert console._active_tool_name is None

    rendered = output.getvalue()
    assert response.content == "# CreatorOS\n\nRich output"
    assert "CreatorOS" in rendered
    assert "████" in rendered
    assert "Rich output" in rendered
    assert "正在调用 read_file" not in rendered
    assert "✓ read_file" in rendered
    assert "done · ok" not in rendered
    assert "[Tool call]" not in rendered
    assert "[Tool result]" not in rendered
    assert "learning build" not in rendered
    assert "┌" not in rendered
    assert "╭" not in rendered
    assert "\033[" not in rendered

    console.render_event(AgentEvent("tool_call", {"name": "write_file"}))
    console.render_event(
        AgentEvent(
            "tool_result",
            {"name": "write_file", "is_error": True, "error_type": "file_exists"},
        )
    )
    assert "✗ write_file · file_exists" in output.getvalue()

    output = StringIO()
    console = RichConsole(output=output)
    console.render_event(AgentEvent("turn_start", {}))
    stream_llm(FragmentProvider(), empty_context, console=console)
    streamed = output.getvalue()
    assert streamed.count("搞定！") == 1
    assert streamed.count("下一段只应追加一次") == 1
    print("rich_console_smoke=passed")


if __name__ == "__main__":
    main()
