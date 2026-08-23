from io import StringIO

from creatoros.agent.streaming import stream_llm
from creatoros.ai.types import StreamEnd, TextDelta
from creatoros.terminal import Console


class FakeStreamingProvider:
    def stream(self, messages, tools):
        yield TextDelta("streamed")
        yield StreamEnd("stop")


def main():
    output = StringIO()
    prompts = []

    def fake_input(prompt):
        prompts.append(prompt)
        return "hello"

    console = Console(input_fn=fake_input, output=output)

    assert console.prompt() == "hello"
    assert prompts == ["❯ "]
    console.write("Agent: ", end="", flush=True)
    console.write("answer")
    console.banner()

    response = stream_llm(FakeStreamingProvider(), [], [], console=console)

    rendered = output.getvalue()
    assert rendered.startswith("Agent: answer\n")
    assert response.content == "streamed"
    assert "streamed\n" in rendered
    assert "CreatorOS Agent Runtime" in rendered
    print("console_smoke=passed")


if __name__ == "__main__":
    main()
