import os
from pathlib import Path
from tempfile import TemporaryDirectory

from dotenv import load_dotenv

from creatoros.agent.loop import build_model_context
from creatoros.ai.deepseek import DeepSeekProvider
from creatoros.session import snapshot
from creatoros.tools import execute_tool_call, tool_registry


def main():
    load_dotenv()
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY。")

    secret = "RETRIEVAL_CODE_8264"
    source = "A" * 9_000 + secret + "B" * 9_000
    messages = [
        {"role": "system", "content": "Use read_tool_result when instructed."},
        {"role": "user", "content": "A tool produced a long result."},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call-source",
                "name": "capture_output",
                "arguments": "{}",
            }],
        },
        {"role": "tool", "tool_call_id": "call-source", "content": source},
        {
            "role": "user",
            "content": (
                "The verification code is hidden near character 9001. "
                "Call read_tool_result with result_ref=call-source, "
                "offset=8950, limit=200, then answer with only the code."
            ),
        },
    ]
    tool_schema = tool_registry["read_tool_result"].to_schema()
    provider = DeepSeekProvider(api_key=api_key)
    original_session_file = snapshot.SESSION_FILE

    with TemporaryDirectory() as directory:
        snapshot.SESSION_FILE = Path(directory) / "latest.json"
        snapshot.save_messages(messages)
        try:
            first = provider.complete(build_model_context(messages, [tool_schema]))
            assert len(first.tool_calls) == 1
            assert first.tool_calls[0].name == "read_tool_result"

            messages.append(first.to_message())
            snapshot.save_messages(messages)
            retrieved = execute_tool_call(first.tool_calls[0])
            assert secret in retrieved.content
            messages.append({
                "role": "tool",
                "tool_call_id": first.tool_calls[0].id,
                "content": retrieved.to_model_content(),
            })
            snapshot.save_messages(messages)

            final = provider.complete(build_model_context(messages, [tool_schema]))
            assert not final.tool_calls
            assert final.content is not None and secret in final.content
            assert snapshot.load_messages()[3]["content"] == source
            print("live_read_tool_result=passed")
            if first.usage and final.usage:
                print(f"input_tokens={first.usage.input_tokens + final.usage.input_tokens}")
                print(f"output_tokens={first.usage.output_tokens + final.usage.output_tokens}")
        finally:
            snapshot.SESSION_FILE = original_session_file


if __name__ == "__main__":
    main()
