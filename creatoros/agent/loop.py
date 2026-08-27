from typing import Callable

from ..ai.context import (
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_RESERVE_OUTPUT_TOKENS,
    ContextBudget,
    ModelContext,
    project_tool_results_for_model,
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
) -> ModelContext:
    active_messages = (
        checkpoint.project_messages(messages) if checkpoint else messages
    )
    projected_messages = project_tool_results_for_model(active_messages)
    return ModelContext.from_messages(projected_messages, tools)


def run_agent(
    provider: ModelProvider,
    on_stream_event: Callable[[RuntimeStreamEvent], None] | None = None,
    max_turns: int = DEFAULT_MAX_TURNS,
    console: Console | None = None,
    on_agent_event: Callable[[AgentEvent], None] | None = None,
):
    console = console or Console()

    def emit(event: AgentEvent):
        console.render_event(event)
        if on_agent_event is not None:
            on_agent_event(event)

    guard = MaxTurnGuard(max_turns)
    runtime_context = RuntimeContext.from_defaults()
    state = AgentState(messages=load_messages())
    checkpoint = load_compaction_checkpoint(state.messages)
    save_messages(state.messages)

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
                    tools,
                    checkpoint,
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
                clear_compaction_checkpoint()
                save_messages(state.messages)
                emit(AgentEvent("session_reset", {}))
                continue

            state.messages.append({"role": "user", "content": user_input})
            save_messages(state.messages)
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
                    tools,
                    checkpoint,
                )
                context_budget = _context_budget_for(provider, model_context)
                if context_budget.needs_attention:
                    compacted = compact_session(
                        provider,
                        state.messages,
                        tools,
                        checkpoint=checkpoint,
                    )
                    if compacted is not None:
                        tokens_before = context_budget.input_tokens
                        checkpoint = compacted
                        model_context = build_model_context(
                            state.messages,
                            tools,
                            checkpoint,
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
                save_messages(state.messages)

                if not response.tool_calls:
                    state.status = "idle"
                    break

                for tool_call in response.tool_calls:
                    emit(AgentEvent("tool_call", {"name": tool_call.name}))
                    tool_result = execute_tool_call(tool_call, context=runtime_context)
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
                    save_messages(state.messages)
    except KeyboardInterrupt:
        emit(AgentEvent("session_saved", {}))
    finally:
        save_messages(state.messages)
