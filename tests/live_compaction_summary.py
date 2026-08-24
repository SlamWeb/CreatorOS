import os

from dotenv import load_dotenv

from creatoros.agent.compaction_summary import (
    CompactionSummaryRequest,
    generate_compaction_summary,
)
from creatoros.ai.deepseek import DeepSeekProvider


def main():
    load_dotenv()
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY。")

    messages = [
        {"role": "user", "content": "记录 CreatorOS 压缩方案。"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-summary-1",
                    "name": "read_file",
                    "arguments": '{"path":"D:\\\\CreatorOS\\\\SPEC.md"}',
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-summary-1",
            "content": (
                "status=200\ntrace_id=trace-creatoros-42\n"
                + "x" * 4_100
            ),
        },
        {
            "role": "assistant",
            "content": "已决定保留完整 recent turns，并摘要旧 turns。",
        },
    ]
    request = CompactionSummaryRequest.from_messages(
        messages,
        previous_summary="## Goal\n从零学习 CreatorOS Agent Runtime",
        custom_instructions=(
            "必须保留精确路径 D:\\CreatorOS\\SPEC.md 和 "
            "trace_id=trace-creatoros-42；工具调用已经成功完成，"
            "摘要输入截断不代表读取失败"
        ),
    )
    result = generate_compaction_summary(
        DeepSeekProvider(api_key=api_key),
        request,
    )

    assert "D:\\CreatorOS\\SPEC.md" in result.markdown
    assert "trace-creatoros-42" in result.markdown
    assert result.truncated_tool_results == 1
    assert result.usage is not None
    print("live_compaction_summary=passed")
    print(f"input_tokens={result.usage.input_tokens}")
    print(f"output_tokens={result.usage.output_tokens}")
    print(result.markdown)


if __name__ == "__main__":
    main()
