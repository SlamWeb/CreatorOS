from typing import Callable
from pathlib import Path
from dataclasses import replace

from ..ai.context import (
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_RESERVE_OUTPUT_TOKENS,
    ContextBudget,
    ModelContext,
)
from ..ai.provider import ModelProvider
from ..ai.types import ModelResponse, RuntimeStreamEvent
from ..commands import render_command_help
from ..session.checkpoint import (
    CompactionCheckpoint,
    clear_compaction_checkpoint,
    load_compaction_checkpoint,
)
from ..session.snapshot import load_messages, new_messages, save_messages
from ..tools import execute_tool_call, tools
from ..context import RuntimeContext
from ..terminal import Console
from ..events import AgentEvent
from ..skills.loader import SkillLoader
from ..session import snapshot
from ..session.artifacts import externalize
from .guards import DEFAULT_MAX_TURNS, MaxTurnGuard
from .compactor import compact_session
from .state import AgentState
from .streaming import stream_llm


def llm(
    provider: ModelProvider,
    context: ModelContext,
) -> ModelResponse:
    return provider.complete(context)


def _context_budget_for(provider: ModelProvider, model_context: ModelContext):
    return ContextBudget.from_context(
        model_context,
        context_window=(
            getattr(provider, "context_window", None) or DEFAULT_CONTEXT_WINDOW
        ),
        reserve_output_tokens=(
            getattr(provider, "reserve_output_tokens", None)
            or DEFAULT_RESERVE_OUTPUT_TOKENS
        ),
    )


def _write_context_status(console: Console, budget: ContextBudget) -> None:
    console.context_status(
        budget.input_tokens,
        budget.input_limit,
        budget.measurement,
    )


def build_model_context(
    messages,
    tools,
    checkpoint: CompactionCheckpoint | None = None,
    skill_loader: SkillLoader | None = None,
) -> ModelContext:
    active_messages = (
        checkpoint.project_messages(messages) if checkpoint else messages
    )
    projected_messages = list(active_messages)
    if skill_loader is not None:
        projected_messages = skill_loader.inject_available_skills(projected_messages)
    return ModelContext.from_messages(projected_messages, tools)


def run_agent(
    provider: ModelProvider,
    on_stream_event: Callable[[RuntimeStreamEvent], None] | None = None,
    max_turns: int = DEFAULT_MAX_TURNS,
    console: Console | None = None,
    on_agent_event: Callable[[AgentEvent], None] | None = None,
    *,
    session_file: Path | None = None,
    runtime_context: RuntimeContext | None = None,
):
    console = console or Console()

    def emit(event: AgentEvent):
        console.render_event(event)
        if on_agent_event is not None:
            on_agent_event(event)

    guard = MaxTurnGuard(max_turns)
    runtime_context = runtime_context or RuntimeContext.from_defaults()
    # The loop owns the active ledger; a caller cannot redirect tool reads elsewhere.
    runtime_context = replace(runtime_context, session_file=Path(session_file or snapshot.SESSION_FILE).resolve())
    allowed = runtime_context.allowed_tools
    model_tools = tools if allowed is None else [t for t in tools if t["function"]["name"] in allowed]
    skill_loader = SkillLoader.from_defaults() if not runtime_context.archive_only_reads and (allowed is None or "read_file" in allowed) else SkillLoader([])
    # Default call shapes remain compatible with CLI hosts and existing tests.
    persist = save_messages if session_file is None else lambda messages: save_messages(messages, session_file)
    state = AgentState(messages=load_messages() if session_file is None else load_messages(session_file))
    checkpoint = (load_compaction_checkpoint(state.messages) if session_file is None
                  else load_compaction_checkpoint(state.messages, session_file))
    persist(state.messages)

    try:
        while True:
            user_input = console.prompt()

            if user_input in {"/menu", "/exit"}:
                break

            if user_input in {"/help", "?"}:
                console.write(render_command_help())
                continue

            if user_input == "/context":
                current_context = build_model_context(
                    state.messages,
                    model_tools,
                    checkpoint,
                    skill_loader,
                )
                _write_context_status(
                    console,
                    _context_budget_for(provider, current_context),
                )
                continue

            if not user_input.strip():
                continue

            if user_input == "/reset":
                state = AgentState(messages=new_messages())
                checkpoint = None
                if session_file is None:
                    clear_compaction_checkpoint()
                else:
                    clear_compaction_checkpoint(session_file)
                persist(state.messages)
                emit(AgentEvent("session_reset", {}))
                continue

            state.messages.append({"role": "user", "content": user_input})
            persist(state.messages)
            state.status = "running"
            task_start_turn = state.turn

            while True:
                turns_used = state.turn - task_start_turn
                if guard.should_stop(turns_used):
                    state.status = "idle"
                    emit(AgentEvent("guard_stop", {"max_turns": max_turns}))
                    break

                state.turn += 1
                emit(AgentEvent("turn_start", {"turn": state.turn}))
                model_context = build_model_context(
                    state.messages,
                    model_tools,
                    checkpoint,
                    skill_loader,
                )
                context_budget = _context_budget_for(provider, model_context)
                if context_budget.needs_attention:
                    compacted = None
                    try:
                        compacted = compact_session(
                            provider,
                            state.messages,
                            model_tools,
                            checkpoint=checkpoint,
                            **({"session_file": session_file} if session_file is not None else {}),
                        )
                    except Exception:
                        # Do not leak upstream errors or replace the last valid checkpoint.
                        emit(AgentEvent("context_warning", {
                            **context_budget.to_event_data(), "compaction_failed": True}))
                        console.write("摘要未完成或未减少输入，已保留原始会话与已有检查点。")
                    if compacted is not None:
                        tokens_before = context_budget.input_tokens
                        checkpoint = compacted
                        model_context = build_model_context(
                            state.messages,
                            model_tools,
                            checkpoint,
                            skill_loader,
                        )
                        context_budget = _context_budget_for(provider, model_context)
                        emit(
                            AgentEvent(
                                "context_compacted",
                                {
                                    "tokens_before": tokens_before,
                                    "tokens_after": context_budget.input_tokens,
                                },
                            )
                        )
                    if context_budget.needs_attention:
                        emit(
                            AgentEvent(
                                "context_warning",
                                context_budget.to_event_data(),
                            )
                        )
                if context_budget.is_over_limit:
                    # Last resort: externalize largest tool bodies, never user text.
                    active = checkpoint.project_messages(state.messages) if checkpoint else list(state.messages)
                    active = [dict(m) for m in active]
                    indices = sorted((i for i, m in enumerate(active) if m.get("role") == "tool"),
                                     key=lambda i: len(active[i].get("content", "")), reverse=True)
                    for index in indices:
                        if len(active[index].get("content", "")) < 2000:
                            continue
                        active[index] = externalize([active[index]], runtime_context.session_file)[0]
                        model_context = build_model_context(active, model_tools, skill_loader=skill_loader)
                        context_budget = _context_budget_for(provider, model_context)
                        if not context_budget.is_over_limit:
                            break
                if context_budget.is_over_limit:
                    message = "上下文超过估算输入预算，本轮已停止，未发送主模型请求。历史已保留；请新建会话并缩短输入，不会自动删除历史。"
                    emit(AgentEvent("context_blocked", {**context_budget.to_event_data(), "message": message}))
                    console.write(message)
                    state.status = "idle"
                    break
                response = stream_llm(
                    provider=provider,
                    context=model_context,
                    on_event=on_stream_event,
                    console=console,
                )
                if response.usage is not None:
                    emit(AgentEvent("model_usage", response.usage.to_dict()))
                    measured_budget = context_budget.with_usage(response.usage)
                    if measured_budget.needs_attention and not context_budget.needs_attention:
                        emit(
                            AgentEvent(
                                "context_warning",
                                measured_budget.to_event_data(),
                            )
                        )

                state.messages.append(response.to_message())
                persist(state.messages)

                if not response.tool_calls:
                    state.status = "idle"
                    break

                for tool_call in response.tool_calls:
                    emit(AgentEvent("tool_call", {"name": tool_call.name}))
                    tool_result = execute_tool_call(tool_call, context=runtime_context, model_requested=True)
                    emit(
                        AgentEvent(
                            "tool_result",
                            {
                                "name": tool_call.name,
                                "content": tool_result.content,
                                "is_error": tool_result.is_error,
                                "error_type": tool_result.error_type,
                            },
                        )
                    )

                    task_id = tool_result.details.get("task_id")
                    if (
                        tool_call.name in {"add_author", "get_author_job", "wait_author_job"}
                        and isinstance(task_id, str)
                        and task_id
                    ):
                        task = state.register_remote_task(
                            task_id=task_id,
                            kind=str(tool_result.details.get("kind") or "external"),
                            remote_status=str(tool_result.details.get("status") or "queued"),
                            progress=(
                                tool_result.details.get("label")
                                or tool_result.details.get("stage")
                            ),
                            error=tool_result.details.get("error_message"),
                        )
                        emit(
                            AgentEvent(
                                "task_updated",
                                {
                                    "task_id": task.task_id,
                                    "kind": task.kind,
                                    "status": task.status.value,
                                    "progress": task.progress,
                                },
                            )
                        )

                    state.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": tool_result.to_model_content(),
                        }
                    )
                    persist(state.messages)
                    externalize([{"role": "assistant", "tool_calls": [{"id": tool_call.id, "name": tool_call.name}]},
                                 state.messages[-1]], runtime_context.session_file)
    except KeyboardInterrupt:
        emit(AgentEvent("session_saved", {}))
    finally:
        persist(state.messages)
