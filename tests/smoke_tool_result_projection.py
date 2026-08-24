from copy import deepcopy

from creatoros.agent.loop import build_model_context
from creatoros.ai.context import (
    MAX_MODEL_TOOL_RESULT_CHARS,
    project_tool_results_for_model,
)


def main():
    long_result = "HEAD" + "x" * 32 + "TAIL"
    messages = [
        {"role": "system", "content": "stable"},
        {"role": "user", "content": "run tool"},
        {"role": "tool", "tool_call_id": "call-42", "content": long_result},
        {"role": "assistant", "content": "done"},
    ]
    original = deepcopy(messages)

    projected = project_tool_results_for_model(
        messages, max_tool_result_chars=20
    )
    content = projected[2]["content"]
    assert content.startswith(long_result[:10])
    assert content.endswith(long_result[-10:])
    assert "20 chars omitted from middle" in content
    assert "result_ref=call-42" in content
    assert messages == original

    short = project_tool_results_for_model(messages, 100)
    assert short == messages

    large_messages = deepcopy(messages)
    large_messages[2]["content"] = (
        "FIRST" + "y" * (MAX_MODEL_TOOL_RESULT_CHARS + 1_000) + "LAST"
    )
    context = build_model_context(large_messages, tools=[])
    request_messages, _ = context.to_request()
    assert "model-context projection" in request_messages[2]["content"]
    assert "FIRST" in request_messages[2]["content"]
    assert "LAST" in request_messages[2]["content"]
    assert len(request_messages[2]["content"]) < len(large_messages[2]["content"])
    assert "model-context projection" not in large_messages[2]["content"]
    assert messages == original
    print("tool_result_projection_smoke=passed")


if __name__ == "__main__":
    main()
