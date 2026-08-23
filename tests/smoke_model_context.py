from creatoros.agent.loop import llm
from creatoros.ai.context import ModelContext
from creatoros.ai.types import ModelResponse


class FakeProvider:
    def complete(self, context):
        self.context = context
        return ModelResponse(content="ok", tool_calls=[])


def main():
    history = [
        {"role": "system", "content": "stable instructions"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    schemas = [{"type": "function", "function": {"name": "read_file"}}]
    context = ModelContext.from_messages(history, schemas)

    assert context.system_messages == (history[0],)
    assert context.messages == tuple(history[1:])
    request_messages, request_tools = context.to_request()
    assert request_messages == history
    assert request_tools == schemas

    history[0]["content"] = "changed"
    schemas[0]["function"]["name"] = "changed"
    assert context.system_messages[0]["content"] == "stable instructions"
    assert context.tools[0]["function"]["name"] == "read_file"

    provider = FakeProvider()
    assert llm(provider, context).content == "ok"
    assert provider.context is context
    print("model_context_smoke=passed")


if __name__ == "__main__":
    main()
