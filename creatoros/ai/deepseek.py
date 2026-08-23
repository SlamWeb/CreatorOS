from openai import OpenAI

from .context import ModelContext
from .types import (
    ModelResponse,
    ModelUsage,
    StreamEnd,
    TextDelta,
    ToolCall,
    ToolCallDelta,
)


DEEPSEEK_CONTEXT_WINDOW = 1_000_000
DEEPSEEK_RESERVE_OUTPUT_TOKENS = 32_768


class DeepSeekProvider:
    def __init__(
        self,
        api_key,
        model="deepseek-v4-flash",
        context_window=DEEPSEEK_CONTEXT_WINDOW,
        reserve_output_tokens=DEEPSEEK_RESERVE_OUTPUT_TOKENS,
    ):
        self.client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
        self.model = model
        self.context_window = context_window
        self.reserve_output_tokens = reserve_output_tokens

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

    @staticmethod
    def _usage_value(usage, name):
        if usage is None:
            return None
        if isinstance(usage, dict):
            return usage.get(name)
        return getattr(usage, name, None)

    @classmethod
    def _to_model_usage(cls, usage):
        input_tokens = cls._usage_value(usage, "prompt_tokens")
        output_tokens = cls._usage_value(usage, "completion_tokens")
        if input_tokens is None or output_tokens is None:
            return None
        total_tokens = cls._usage_value(usage, "total_tokens")
        cache_hit = cls._usage_value(usage, "prompt_cache_hit_tokens")
        cache_miss = cls._usage_value(usage, "prompt_cache_miss_tokens")
        return ModelUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens or input_tokens + output_tokens,
            cache_hit_tokens=cache_hit,
            cache_miss_tokens=cache_miss,
        )

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
            usage=self._to_model_usage(getattr(response, "usage", None)),
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
        stream_usage = None
        for chunk in response_stream:
            chunk_usage = self._to_model_usage(getattr(chunk, "usage", None))
            if chunk_usage is not None:
                stream_usage = chunk_usage
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

        yield StreamEnd(finish_reason=finish_reason, usage=stream_usage)
