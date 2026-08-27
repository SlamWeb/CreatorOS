from io import StringIO

from creatoros.agent import loop as agent_loop
from creatoros.agent.loop import run_agent
from creatoros.terminal import Console


class FakeProvider:
    def __init__(self):
        self.calls = 0

    def stream(self, context):
        self.calls += 1
        yield from ()


def main():
    original_load = agent_loop.load_messages
    original_save = agent_loop.save_messages
    agent_loop.load_messages = lambda: [{"role": "system", "content": "test"}]
    agent_loop.save_messages = lambda messages: None
    try:
        inputs = iter(["/help", "/menu"])
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
    assert "/menu 返回菜单" in output.getvalue()
    assert "/help" not in output.getvalue().split("/menu 返回菜单", 1)[-1]
    print("agent_navigation_smoke=passed")


if __name__ == "__main__":
    main()
