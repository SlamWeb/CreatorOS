import os

from dotenv import load_dotenv

from creatoros.agent.loop import build_model_context
from creatoros.ai.deepseek import DeepSeekProvider


def main():
    load_dotenv()
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY。")

    full_result = "HEAD_MARKER\n" + "x" * 20_000 + "\nTAIL_MARKER"
    messages = [
        {"role": "system", "content": "Answer briefly and do not call tools."},
        {"role": "user", "content": "Inspect the tool output."},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call-live-1",
                "name": "capture_output",
                "arguments": "{}",
            }],
        },
        {"role": "tool", "tool_call_id": "call-live-1", "content": full_result},
        {
            "role": "user",
            "content": "Reply with HEAD_MARKER and TAIL_MARKER only.",
        },
    ]
    context = build_model_context(messages, tools=[])
    request_messages, _ = context.to_request()
    projected = request_messages[3]["content"]
    assert len(projected) < len(full_result)
    assert "HEAD_MARKER" in projected and "TAIL_MARKER" in projected
    assert messages[3]["content"] == full_result

    response = DeepSeekProvider(api_key=api_key).complete(context)
    assert response.content is not None
    assert "HEAD_MARKER" in response.content
    assert "TAIL_MARKER" in response.content
    print("live_tool_result_projection=passed")
    if response.usage:
        print(f"input_tokens={response.usage.input_tokens}")
        print(f"output_tokens={response.usage.output_tokens}")


if __name__ == "__main__":
    main()
