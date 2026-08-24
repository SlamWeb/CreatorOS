from creatoros.agent.compaction import CompactionPlan, calculate_keep_recent_tokens
from creatoros.ai.context import ModelContext, estimate_tokens
from creatoros.ai.deepseek import (
    DEEPSEEK_CONTEXT_WINDOW,
    DEEPSEEK_RESERVE_OUTPUT_TOKENS,
)
from main import CompactionPlan as RootCompactionPlan


def main():
    assert RootCompactionPlan is CompactionPlan
    deepseek_input_limit = (
        DEEPSEEK_CONTEXT_WINDOW - DEEPSEEK_RESERVE_OUTPUT_TOKENS
    )
    assert deepseek_input_limit == 967_232
    assert calculate_keep_recent_tokens(deepseek_input_limit) == 120_904
    assert calculate_keep_recent_tokens(28_672) == 8_000
    assert calculate_keep_recent_tokens(4_000) == 4_000
    first_turn = [
        {"role": "user", "content": "读取文件"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "1", "name": "read_file", "arguments": "{}"}],
        },
        {"role": "tool", "tool_call_id": "1", "content": "文件内容"},
        {"role": "assistant", "content": "读取完成"},
    ]
    recent_turn = [
        {"role": "user", "content": "总结一下"},
        {"role": "assistant", "content": "总结结果"},
    ]
    history = [{"role": "system", "content": "stable"}, *first_turn, *recent_turn]
    context = ModelContext.from_messages(history, [{"type": "function"}])

    plan = CompactionPlan.from_context(
        context,
        input_limit=deepseek_input_limit,
        keep_recent_tokens=estimate_tokens(recent_turn),
    )
    assert plan.messages_to_summarize == tuple(first_turn)
    assert plan.retained_messages == tuple(recent_turn)
    assert plan.first_retained_index == len(first_turn)
    assert plan.can_compact
    assert plan.messages_to_summarize[1]["tool_calls"][0]["id"] == "1"
    assert plan.messages_to_summarize[2]["tool_call_id"] == "1"

    roomy = CompactionPlan.from_context(
        context,
        input_limit=deepseek_input_limit,
        keep_recent_tokens=100_000,
    )
    assert not roomy.can_compact
    assert roomy.retained_messages == tuple(first_turn + recent_turn)

    oversized = CompactionPlan.from_context(
        context,
        input_limit=deepseek_input_limit,
        keep_recent_tokens=1,
    )
    assert oversized.retained_messages == tuple(recent_turn)
    assert oversized.retained_turn_exceeds_budget

    empty = ModelContext.from_messages([history[0]], [])
    dynamic = CompactionPlan.from_context(empty, input_limit=deepseek_input_limit)
    assert not dynamic.can_compact
    assert dynamic.input_limit == deepseek_input_limit
    assert dynamic.keep_recent_tokens == 120_904

    try:
        CompactionPlan.from_context(
            context,
            input_limit=deepseek_input_limit,
            keep_recent_tokens=0,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("零预算应该被拒绝")

    for invalid_input_limit in (0, -1):
        try:
            CompactionPlan.from_context(context, input_limit=invalid_input_limit)
        except ValueError:
            pass
        else:
            raise AssertionError("非正数 input_limit 应该被拒绝")

    try:
        CompactionPlan.from_context(
            context,
            input_limit=8_000,
            keep_recent_tokens=8_001,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("保留预算不应该超过可用输入窗口")

    print("compaction_plan_smoke=passed")


if __name__ == "__main__":
    main()
