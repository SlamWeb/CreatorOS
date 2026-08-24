import json
from pathlib import Path
from tempfile import TemporaryDirectory

from creatoros.ai.types import ToolCall
from creatoros.session import snapshot
from creatoros.session.snapshot import find_tool_result
from creatoros.tools import execute_tool_call, read_tool_result, tool_registry


def main():
    original_session_file = snapshot.SESSION_FILE
    content = "0123456789ABCDEFGHIJ"

    with TemporaryDirectory() as directory:
        snapshot.SESSION_FILE = Path(directory) / "latest.json"
        messages = [
            {"role": "system", "content": "test"},
            {"role": "user", "tool_call_id": "call-user", "content": "private"},
            {"role": "tool", "tool_call_id": "call-1", "content": content},
        ]
        snapshot.save_messages(messages)
        try:
            assert find_tool_result(messages, "call-1") == messages[-1]
            assert find_tool_result(messages, "call-user") is None

            page = read_tool_result("call-1", offset=6, limit=5)
            assert not page.is_error
            assert "chars=6-10 of 20" in page.content
            assert "56789" in page.content
            assert "next_offset=11" in page.content

            call = ToolCall(
                "call-read",
                "read_tool_result",
                json.dumps({"result_ref": "call-1", "offset": 11, "limit": 10}),
            )
            result = execute_tool_call(call)
            assert "ABCDEFGHIJ" in result.content
            assert not result.is_error

            missing = read_tool_result("missing")
            assert missing.error_type == "tool_result_not_found"
            outside = read_tool_result("call-1", offset=99)
            assert outside.error_type == "offset_out_of_range"
            invalid = read_tool_result("call-1", limit=16_001)
            assert invalid.error_type == "invalid_arguments"

            schema = tool_registry["read_tool_result"].to_schema()["function"]
            assert schema["parameters"]["properties"]["limit"]["maximum"] == 16_000
            stored = json.loads(snapshot.SESSION_FILE.read_text(encoding="utf-8"))
            assert stored[-1]["content"] == content
        finally:
            snapshot.SESSION_FILE = original_session_file

    print("read_tool_result_smoke=passed")


if __name__ == "__main__":
    main()
