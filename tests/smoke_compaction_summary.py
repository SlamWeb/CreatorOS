from creatoros.agent.compaction_summary import (
    MAX_SUMMARY_TOOL_RESULT_CHARS,
    CompactionSummaryRequest,
    serialize_messages_for_summary,
    validate_summary_markdown,
)
from main import CompactionSummaryRequest as RootCompactionSummaryRequest


def main():
    assert RootCompactionSummaryRequest is CompactionSummaryRequest
    long_result = (
        "HEAD"
        + "x" * (MAX_SUMMARY_TOOL_RESULT_CHARS + 17)
        + "TAIL"
    )
    messages = [
        {"role": "user", "content": "读取 SPEC.md"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "name": "read_file",
                    "arguments": '{"path":"SPEC.md"}',
                },
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": long_result},
        {"role": "assistant", "content": "读取完成"},
    ]
    request = CompactionSummaryRequest.from_messages(
        messages,
        previous_summary="## Goal\n学习 Agent Runtime",
        custom_instructions="重点保留 Context 决策",
    )

    assert request.source_message_count == 4
    assert request.truncated_tool_results == 1
    assert not request.context.tools
    assert len(request.context.system_messages) == 1
    assert len(request.context.messages) == 1
    prompt = request.context.messages[0]["content"]
    assert "[User]\n读取 SPEC.md" in prompt
    assert "read_file" in prompt and 'SPEC.md' in prompt
    assert "[Tool result id=call-1]" in prompt
    assert "[summary-input projection: 25 chars omitted from middle" in prompt
    assert "full result retained in session; result_ref=call-1]" in prompt
    assert "HEAD" in prompt and "TAIL" in prompt
    assert long_result not in prompt
    assert "学习 Agent Runtime" in prompt
    assert "重点保留 Context 决策" in prompt

    try:
        CompactionSummaryRequest.from_messages([])
    except ValueError:
        pass
    else:
        raise AssertionError("空消息不应该生成摘要请求")

    try:
        serialize_messages_for_summary(messages, max_tool_result_chars=0)
    except ValueError:
        pass
    else:
        raise AssertionError("非正数工具结果上限应该被拒绝")

    valid_summary = "\n".join(
        [
            "## Goal",
            "## Constraints & Preferences",
            "## Progress",
            "### Done",
            "### In Progress",
            "### Blocked",
            "## Key Decisions",
            "## Important Facts & IDs",
            "## Files & Artifacts",
            "## Next Steps",
            "## Unresolved Questions",
        ]
    )
    assert validate_summary_markdown(valid_summary) == valid_summary

    try:
        validate_summary_markdown("## Goal\n只有一个标题")
    except ValueError:
        pass
    else:
        raise AssertionError("缺少必要标题的摘要应该被拒绝")

    print("compaction_summary_smoke=passed")


if __name__ == "__main__":
    main()
