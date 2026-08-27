from io import StringIO

from creatoros.agent import loop as agent_loop
from creatoros.agent.loop import run_agent
from creatoros.terminal import Console, RichConsole


class FakeProvider:
    def __init__(self):
        self.calls = 0

    def stream(self, context):
        self.calls += 1
        yield from ()


def main():
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    completer = RichConsole._build_slash_completer()
    all_commands = [
        item.text
        for item in completer.get_completions(Document("/"), CompleteEvent())
    ]
    assert all_commands == ["/help", "/menu", "/context", "/reset", "/exit"]
    context_commands = [
        item.text
        for item in completer.get_completions(Document("/c"), CompleteEvent())
    ]
    assert context_commands == ["/context"]

    original_load = agent_loop.load_messages
    original_save = agent_loop.save_messages
    agent_loop.load_messages = lambda: [{"role": "system", "content": "test"}]
    agent_loop.save_messages = lambda messages: None
    try:
        inputs = iter(["/help", "/context", "/menu"])
        output = StringIO()
        provider = FakeProvider()
        run_agent(
            provider,
            console=Console(input_fn=lambda prompt: next(inputs), output=output),
        )
    finally:
        agent_loop.load_messages = original_load
        agent_loop.save_messages = original_save

    assert provider.calls == 0
    rendered = output.getvalue()
    assert "/context  查看当前上下文使用量" in rendered
    assert "◌ 上下文" in rendered
    assert "tokens · estimate" in rendered
    assert "窗口 1,000,000" not in rendered
    assert "输出预留" not in rendered
    print("agent_navigation_smoke=passed")


if __name__ == "__main__":
    main()
