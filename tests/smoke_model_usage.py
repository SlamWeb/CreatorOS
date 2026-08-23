from io import StringIO
from types import SimpleNamespace

from creatoros.agent.streaming import stream_llm
from creatoros.ai.context import ModelContext
from creatoros.ai.deepseek import DeepSeekProvider
from creatoros.ai.types import ModelUsage, StreamEnd
from creatoros.terminal import Console


class FakeCompletions:
    def create(self, **kwargs):
        if not kwargs.get("stream"):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="complete", tool_calls=[])
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=10,
                    completion_tokens=4,
                    total_tokens=14,
                ),
            )
        return iter(
            [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            finish_reason="stop",
                            delta=SimpleNamespace(content="hello", tool_calls=[]),
                        )
                    ],
                    usage=None,
                ),
                SimpleNamespace(
                    choices=[],
                    usage=SimpleNamespace(
                        prompt_tokens=120,
                        completion_tokens=8,
                        total_tokens=128,
                        prompt_cache_hit_tokens=64,
                        prompt_cache_miss_tokens=56,
                    ),
                ),
            ]
        )


def main():
    provider = DeepSeekProvider(api_key="test")
    assert provider.context_window == 1_000_000
    assert provider.reserve_output_tokens == 32_768
    provider.client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )
    context = ModelContext.from_messages([], [])
    complete = provider.complete(context)
    assert complete.content == "complete"
    assert complete.usage == ModelUsage(10, 4, 14)
    assert "usage" not in complete.to_message()
    events = list(provider.stream(context))
    assert isinstance(events[-1], StreamEnd)
    assert events[-1].usage == ModelUsage(120, 8, 128, 64, 56)
    output = StringIO()
    response = stream_llm(provider, context, console=Console(output=output))

    assert response.content == "hello"
    assert response.usage == ModelUsage(120, 8, 128, 64, 56)
    assert "hello" in output.getvalue()

    print("model_usage_smoke=passed")


if __name__ == "__main__":
    main()
