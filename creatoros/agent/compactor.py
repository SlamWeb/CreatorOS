from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from ..ai.context import (
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_RESERVE_OUTPUT_TOKENS,
    ContextBudget,
    ModelContext,
)
from ..ai.provider import ModelProvider
from ..session.checkpoint import (
    CompactionCheckpoint,
    save_compaction_checkpoint,
)
from .compaction import CompactionPlan
from ..session import snapshot
from ..session.artifacts import archive_selected_contexts, externalize, index_note
from ..session.context_trace import ContextTrace, checkpoint_id
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
    trace: ContextTrace | None = None,
    turn_id: str | None = None,
    reserved_input_tokens: int = 0,
) -> CompactionCheckpoint | None:
    raw_messages = deepcopy(list(messages))
    if checkpoint and not checkpoint.matches_session(raw_messages):
        raise ValueError("checkpoint 与当前 Session 不匹配。")

    active_messages = (
        checkpoint.project_messages(raw_messages) if checkpoint else raw_messages
    )
    active_context = ModelContext.from_messages(
        active_messages,
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

    # A pin is a virtual prefix, not another entry in the append-only ledger.
    pin_index = checkpoint.pinned_user_index if checkpoint else None
    pin_indices = ([pin_index, *checkpoint.pinned_host_indices] if pin_index is not None else [])
    if pin_indices:
        live_messages[0:0] = [deepcopy(raw_messages[index]) for index in pin_indices]

    plan_context = ModelContext.from_messages(live_messages, tools=[])
    planning_limit = context_budget.input_limit - reserved_input_tokens
    if planning_limit <= 0:
        raise ValueError("动态上下文本身已超过输入预算。")
    plan = CompactionPlan.from_context(
        plan_context,
        input_limit=planning_limit,
        keep_recent_tokens=keep_recent_tokens,
    )
    if not plan.can_compact:
        return None

    def source_index(index: int) -> int:
        return pin_indices[index] if index < len(pin_indices) else base_index + index - len(pin_indices)

    first_retained_index = source_index(plan.first_retained_index)
    pinned_user_index = (source_index(plan.pinned_user_index)
                         if plan.pinned_user_index is not None else None)
    pinned_host_indices = tuple(source_index(index) for index in plan.pinned_host_indices)
    if pinned_user_index is not None:
        custom_instructions = (custom_instructions or "") + (
            "\nThis checkpoint ends inside an ongoing user turn. Preserve completed "
            "steps, unresolved work and exact facts needed to continue. Do not infer "
            "that the task is complete. The original user request and recent steps "
            "will remain verbatim after this summary."
        )

    archive_session = session_file or snapshot.SESSION_FILE
    summary_source = archive_selected_contexts(plan.messages_to_summarize, archive_session)
    request = CompactionSummaryRequest.from_messages(
        externalize(summary_source, archive_session),
        previous_summary=previous_summary,
        custom_instructions=custom_instructions,
    )
    trace = trace or ContextTrace(session_file or snapshot.SESSION_FILE)
    with trace.request('compaction', provider, turn_id=turn_id or uuid4().hex,
                       checkpoint=checkpoint, source_message_count=len(raw_messages),
                       first_retained_index=first_retained_index,
                       pinned_user_index=pinned_user_index,
                       pinned_host_indices=pinned_host_indices,
                       compacted=False, externalized_count=sum(m.get('role') == 'tool'
                           for m in plan.messages_to_summarize)) as span:
        result = generate_compaction_summary(provider, request, trace=span, previous_summary=previous_summary)
        span.record['stage'] = 'checkpoint_gain_validation'
        new_checkpoint = CompactionCheckpoint.create(
            summary=result.markdown + index_note(session_file or snapshot.SESSION_FILE),
            messages=raw_messages,
            first_retained_index=first_retained_index,
            pinned_user_index=pinned_user_index,
            pinned_host_indices=pinned_host_indices,
            tokens_before=context_budget.input_tokens,
            usage=result.usage,
        )
        new_context = ModelContext.from_messages(
            new_checkpoint.project_messages(raw_messages), tools)
        new_budget = ContextBudget.from_context(new_context,
            context_window=context_budget.context_window,
            reserve_output_tokens=context_budget.reserve_output_tokens)
        span.record.update(tokens_before=context_budget.input_tokens, tokens_after=new_budget.input_tokens)
        if new_budget.input_tokens >= context_budget.input_tokens:
            raise ValueError("摘要没有减少估算输入；保留原有上下文和 checkpoint。")
        span.record['stage'] = 'checkpoint_persistence'
        save_compaction_checkpoint(new_checkpoint, session_file)
        span.record['stage'] = 'complete'
        span.record.update(compacted=True, output_checkpoint_id=checkpoint_id(new_checkpoint))
    return new_checkpoint
