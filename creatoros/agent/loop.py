from typing import Callable

from ..ai.provider import ModelProvider
from ..ai.types import ModelResponse, RuntimeStreamEvent
from ..session.snapshot import load_messages, new_messages, save_messages
from ..tools import execute_tool_call, tools
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
):
    state = AgentState(messages=load_messages())
    save_messages(state.messages)

    try:
        while True:
            user_input = input("你：")

            if user_input == "/exit":
                break

            if user_input == "/reset":
                state = AgentState(messages=new_messages())
                save_messages(state.messages)
                print("[Session] 已清空当前会话。")
                continue

            state.messages.append({"role": "user", "content": user_input})
            save_messages(state.messages)
            state.status = "running"

            while True:
                state.turn += 1
                print("Agent: ", end="", flush=True)
                response = stream_llm(
                    provider=provider,
                    messages=state.messages,
                    tools=tools,
                    on_event=on_stream_event,
                )

                state.messages.append(response.to_message())
                save_messages(state.messages)

                if not response.tool_calls:
                    state.status = "idle"
                    break

                for tool_call in response.tool_calls:
                    print(f"[Tool call] {tool_call.name}")
                    tool_result = execute_tool_call(tool_call)
                    print(f"[Tool result] {tool_result.content}")

                    state.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": tool_result.content,
                        }
                    )
                    save_messages(state.messages)
    except KeyboardInterrupt:
        print("\n[Session] 已保存当前会话。")
    finally:
        save_messages(state.messages)
