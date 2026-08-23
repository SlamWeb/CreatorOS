from typing import Callable

from ..ai.provider import ModelProvider
from ..ai.types import ModelResponse, RuntimeStreamEvent
from ..session.snapshot import load_messages, new_messages, save_messages
from ..tools import execute_tool_call, tools
from ..context import RuntimeContext
from ..terminal import Console
from ..events import AgentEvent
from .guards import DEFAULT_MAX_TURNS, MaxTurnGuard
from .state import AgentState
from .streaming import stream_llm


def llm(
    provider: ModelProvider,
    messages: list[dict],
    tools: list[dict],
) -> ModelResponse:
    return provider.complete(messages=messages, tools=tools)


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
    save_messages(state.messages)

    try:
        while True:
            user_input = console.prompt("你：")

            if user_input == "/exit":
                break

            if user_input == "/reset":
                state = AgentState(messages=new_messages())
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
                response = stream_llm(
                    provider=provider,
                    messages=state.messages,
                    tools=tools,
                    on_event=on_stream_event,
                    console=console,
                )

                state.messages.append(response.to_message())
                save_messages(state.messages)

                if not response.tool_calls:
                    state.status = "idle"
                    break

                for tool_call in response.tool_calls:
                    emit(AgentEvent("tool_call", {"name": tool_call.name}))
                    tool_result = execute_tool_call(tool_call, context=runtime_context)
                    emit(AgentEvent("tool_result", {"content": tool_result.content}))

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
