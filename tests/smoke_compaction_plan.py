from creatoros.agent.compaction import CompactionPlan
from creatoros.ai.context import ModelContext, estimate_tokens
from main import CompactionPlan as RootCompactionPlan


def main():
    assert RootCompactionPlan is CompactionPlan
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
        keep_recent_tokens=estimate_tokens(recent_turn),
    )
    assert plan.messages_to_summarize == tuple(first_turn)
    assert plan.retained_messages == tuple(recent_turn)
    assert plan.first_retained_index == len(first_turn)
    assert plan.can_compact
    assert plan.messages_to_summarize[1]["tool_calls"][0]["id"] == "1"
    assert plan.messages_to_summarize[2]["tool_call_id"] == "1"

    roomy = CompactionPlan.from_context(context, keep_recent_tokens=100_000)
    assert not roomy.can_compact
    assert roomy.retained_messages == tuple(first_turn + recent_turn)

    oversized = CompactionPlan.from_context(context, keep_recent_tokens=1)
    assert oversized.retained_messages == tuple(recent_turn)
    assert oversized.retained_turn_exceeds_budget

    empty = ModelContext.from_messages([history[0]], [])
    assert not CompactionPlan.from_context(empty).can_compact

    try:
        CompactionPlan.from_context(context, keep_recent_tokens=0)
    except ValueError:
        pass
    else:
        raise AssertionError("零预算应该被拒绝")

    print("compaction_plan_smoke=passed")


if __name__ == "__main__":
    main()
