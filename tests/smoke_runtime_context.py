import json
import tempfile
from pathlib import Path

from creatoros.context import RuntimeContext
from creatoros.ai.types import ToolCall
from creatoros.tools import execute_tool_call


def main():
    with tempfile.TemporaryDirectory() as raw_root:
        root = Path(raw_root)
        (root / "hello.txt").write_text("hello from context", encoding="utf-8")
        context = RuntimeContext(root, operating_system="TestOS", shell="TestShell")
        result = execute_tool_call(
            ToolCall("smoke-1", "read_file", json.dumps({"path": "hello.txt"})),
            context=context,
        )
        assert result.content == "hello from context"

    default_context = RuntimeContext.from_defaults()
    assert default_context.project_root.exists()
    assert default_context.operating_system
    assert default_context.shell
    print("runtime_context_smoke=passed")


if __name__ == "__main__":
    main()
