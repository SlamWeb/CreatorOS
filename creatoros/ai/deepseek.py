from openai import OpenAI

from .context import ModelContext
from .types import (
    ModelResponse,
    StreamEnd,
    TextDelta,
    ToolCall,
    ToolCallDelta,
)


class DeepSeekProvider:
    def __init__(self, api_key, model="deepseek-v4-flash"):
        self.client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
        self.model = model

    def _to_openai_messages(self, messages):
        converted = []
        for message in messages:
            if message.get("role") != "assistant":
                converted.append(message)
                continue

            converted_message = dict(message)
            tool_calls = message.get("tool_calls", [])
            if tool_calls:
                converted_message["tool_calls"] = [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": call["arguments"],
                        },
                    }
                    for call in tool_calls
                ]
            converted.append(converted_message)
        return converted

    def complete(self, context: ModelContext):
        messages, tools = context.to_request()
        response = self.client.chat.completions.create(
            model=self.model,
            messages=self._to_openai_messages(messages),
            tools=tools,
            extra_body={"thinking": {"type": "disabled"}},
        )
        assistant_message = response.choices[0].message
        return ModelResponse(
            content=assistant_message.content,
            tool_calls=[
                ToolCall(
                    id=tool_call.id,
                    name=tool_call.function.name,
                    arguments=tool_call.function.arguments,
                )
                for tool_call in assistant_message.tool_calls or []
            ],
        )

    def stream(self, context: ModelContext):
        messages, tools = context.to_request()
        response_stream = self.client.chat.completions.create(
            model=self.model,
            messages=self._to_openai_messages(messages),
            tools=tools,
            stream=True,
            stream_options={"include_usage": True},
            extra_body={"thinking": {"type": "disabled"}},
        )
        finish_reason = None
        for chunk in response_stream:
            if not chunk.choices:
                continue

            choice = chunk.choices[0]
            finish_reason = choice.finish_reason or finish_reason
            delta = choice.delta
            if delta.content:
                yield TextDelta(content=delta.content)

            for tool_call in delta.tool_calls or []:
                function = tool_call.function
                yield ToolCallDelta(
                    index=tool_call.index,
                    id=tool_call.id,
                    name=function.name if function else None,
                    arguments=(function.arguments if function else None) or "",
                )

        yield StreamEnd(finish_reason=finish_reason)
