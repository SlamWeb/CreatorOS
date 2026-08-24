from copy import deepcopy
from pathlib import Path

from ..ai.context import (
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_RESERVE_OUTPUT_TOKENS,
    ContextBudget,
    ModelContext,
    project_tool_results_for_model,
)
from ..ai.provider import ModelProvider
from ..session.checkpoint import (
    CompactionCheckpoint,
    save_compaction_checkpoint,
)
from .compaction import CompactionPlan
from .compaction_summary import (
    CompactionSummaryRequest,
    generate_compaction_summary,
)


_STABLE_ROLES = {"system", "developer"}


def _stable_prefix_count(messages) -> int:
    count = 0
    for message in messages:
        if message.get("role") not in _STABLE_ROLES:
            break
        count += 1
    return count


def compact_session(
    provider: ModelProvider,
    messages,
    tools,
    *,
    checkpoint: CompactionCheckpoint | None = None,
    session_file: Path | None = None,
    keep_recent_tokens: int | None = None,
    custom_instructions: str | None = None,
) -> CompactionCheckpoint | None:
    raw_messages = deepcopy(list(messages))
    if checkpoint and not checkpoint.matches_session(raw_messages):
        raise ValueError("checkpoint 与当前 Session 不匹配。")

    active_messages = (
        checkpoint.project_messages(raw_messages) if checkpoint else raw_messages
    )
    active_context = ModelContext.from_messages(
        project_tool_results_for_model(active_messages),
        tools,
    )
    context_budget = ContextBudget.from_context(
        active_context,
        context_window=(
            getattr(provider, "context_window", None) or DEFAULT_CONTEXT_WINDOW
        ),
        reserve_output_tokens=(
            getattr(provider, "reserve_output_tokens", None)
            or DEFAULT_RESERVE_OUTPUT_TOKENS
        ),
    )

    if checkpoint:
        base_index = checkpoint.first_retained_index
        live_messages = [
            *deepcopy(list(checkpoint.retained_messages)),
            *deepcopy(raw_messages[checkpoint.source_message_count :]),
        ]
        previous_summary = checkpoint.summary
    else:
        base_index = _stable_prefix_count(raw_messages)
        live_messages = deepcopy(raw_messages[base_index:])
        previous_summary = None

    plan_context = ModelContext.from_messages(live_messages, tools=[])
    plan = CompactionPlan.from_context(
        plan_context,
        input_limit=context_budget.input_limit,
        keep_recent_tokens=keep_recent_tokens,
    )
    if not plan.can_compact:
        return None

    request = CompactionSummaryRequest.from_messages(
        plan.messages_to_summarize,
        previous_summary=previous_summary,
        custom_instructions=custom_instructions,
    )
    result = generate_compaction_summary(provider, request)
    new_checkpoint = CompactionCheckpoint.create(
        summary=result.markdown,
        messages=raw_messages,
        first_retained_index=base_index + plan.first_retained_index,
        tokens_before=context_budget.input_tokens,
        usage=result.usage,
    )
    save_compaction_checkpoint(new_checkpoint, session_file)
    return new_checkpoint
