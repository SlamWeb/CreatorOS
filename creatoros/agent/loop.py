from typing import Callable

from ..ai.provider import ModelProvider
from ..ai.types import ModelResponse, RuntimeStreamEvent
from ..session.snapshot import load_messages, new_messages, save_messages
from ..tools import execute_tool_call, tools
from ..context import RuntimeContext
from ..terminal import Console
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
):
    console = console or Console()
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
                console.write("[Session] 已清空当前会话。")
                continue

            state.messages.append({"role": "user", "content": user_input})
            save_messages(state.messages)
            state.status = "running"
            task_start_turn = state.turn

            while True:
                turns_used = state.turn - task_start_turn
                if guard.should_stop(turns_used):
                    state.status = "idle"
                    console.write(f"[Guard] 本次任务已达到最大模型调用次数：{max_turns}")
                    break

                state.turn += 1
                console.write("Agent: ", end="", flush=True)
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
                    console.write(f"[Tool call] {tool_call.name}")
                    tool_result = execute_tool_call(tool_call, context=runtime_context)
                    console.write(f"[Tool result] {tool_result.content}")

                    state.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": tool_result.to_model_content(),
                        }
                    )
                    save_messages(state.messages)
    except KeyboardInterrupt:
        console.write("\n[Session] 已保存当前会话。")
    finally:
        save_messages(state.messages)
