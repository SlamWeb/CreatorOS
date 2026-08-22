from typing import Callable

from ..ai.provider import ModelProvider
from ..ai.types import ModelResponse, RuntimeStreamEvent
from ..session.snapshot import load_messages, new_messages, save_messages
from ..tools import execute_tool_call, tools
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
    messages = load_messages()
    save_messages(messages)

    try:
        while True:
            user_input = input("你：")

            if user_input == "/exit":
                break

            if user_input == "/reset":
                messages = new_messages()
                save_messages(messages)
                print("[Session] 已清空当前会话。")
                continue

            messages.append({"role": "user", "content": user_input})
            save_messages(messages)

            while True:
                print("Agent: ", end="", flush=True)
                response = stream_llm(
                    provider=provider,
                    messages=messages,
                    tools=tools,
                    on_event=on_stream_event,
                )

                messages.append(response.to_message())
                save_messages(messages)

                if not response.tool_calls:
                    break

                for tool_call in response.tool_calls:
                    print(f"[Tool call] {tool_call.name}")
                    tool_result = execute_tool_call(tool_call)
                    print(f"[Tool result] {tool_result}")

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": tool_result,
                        }
                    )
                    save_messages(messages)
    except KeyboardInterrupt:
        print("\n[Session] 已保存当前会话。")
    finally:
        save_messages(messages)
