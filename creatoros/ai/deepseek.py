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
DEEPSEEK_REASONING_EFFORT_MAP = {
    "none": "none", "low": "low", "medium": "high", "high": "high",
    "xhigh": "high", "ultra": "max", "max": "max",
}
DEEPSEEK_REASONING_EFFORTS = tuple(DEEPSEEK_REASONING_EFFORT_MAP)
DEEPSEEK_CHAT_REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "ultra")


class DeepSeekProvider:
    def __init__(
        self,
        api_key,
        model="deepseek-v4-flash",
        context_window=DEEPSEEK_CONTEXT_WINDOW,
        reserve_output_tokens=DEEPSEEK_RESERVE_OUTPUT_TOKENS,
        timeout_seconds=None,
        max_retries=2,
        image_resolver=None,
        reasoning_effort="none",
    ):
        client_options = {"api_key": api_key, "base_url": "https://api.deepseek.com", "max_retries": max_retries}
        if timeout_seconds is not None:
            client_options["timeout"] = timeout_seconds
        self.client = OpenAI(**client_options)
        self.model = model
        self.context_window = context_window
        self.reserve_output_tokens = reserve_output_tokens
        self.image_resolver = image_resolver
        self.set_reasoning_effort(reasoning_effort)

    def set_reasoning_effort(self, effort):
        if effort not in DEEPSEEK_REASONING_EFFORTS:
            raise ValueError("请选择受支持的 DeepSeek 推理程度。")
        self.reasoning_effort = effort
        self.actual_reasoning_effort = DEEPSEEK_REASONING_EFFORT_MAP[effort]

    def _thinking_options(self):
        if self.actual_reasoning_effort == "none":
            return {"extra_body": {"thinking": {"type": "disabled"}}}
        return {"reasoning_effort": self.actual_reasoning_effort,
                "extra_body": {"thinking": {"type": "enabled"}}}

    def _to_openai_messages(self, messages):
        converted = []
        for message in messages:
            message = {key: value for key, value in message.items() if key != "creatoros_source"}
            if message.get("role") != "assistant":
                message = dict(message)
                if isinstance(message.get("content"), list):
                    blocks = []
                    for block in message["content"]:
                        if block.get("type") == "image_ref":
                            if message.get("role") != "user" or self.image_resolver is None:
                                raise ValueError("当前请求未配置安全图片读取。")
                            reference = block["attachment"]
                            blocks.append({"type": "text", "text": f"图片：{reference['name']}；附件ID：{reference['id']}"})
                            blocks.append({"type": "image_url", "image_url": {
                                "url": self.image_resolver(reference), "detail": "original"}})
                        else:
                            blocks.append(block)
                    message["content"] = blocks
                converted.append(message)
                continue

            converted_message = dict(message)
            # Thinking + tools requires complete reasoning round-trips. Old
            # non-thinking history has no reasoning, so explicitly send empty.
            if self.reasoning_effort != "none":
                converted_message.setdefault("reasoning_content", "")
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

    @classmethod
    def _to_responses_usage(cls, usage):
        if usage is None:
            return None
        input_tokens = cls._usage_value(usage, "input_tokens")
        output_tokens = cls._usage_value(usage, "output_tokens")
        if input_tokens is None or output_tokens is None:
            return None
        input_details = cls._usage_value(usage, "input_tokens_details")
        cache_hit = cls._usage_value(input_details, "cached_tokens")
        total_tokens = cls._usage_value(usage, "total_tokens")
        return ModelUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens or input_tokens + output_tokens,
            cache_hit_tokens=cache_hit,
        )

    def complete(self, context: ModelContext):
        messages, tools = context.to_request()
        response = self.client.chat.completions.create(
            model=self.model,
            messages=self._to_openai_messages(messages),
            tools=tools,
            **({"max_tokens": context.max_output_tokens} if context.max_output_tokens is not None else {}),
            **self._thinking_options(),
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
            reasoning_content=getattr(assistant_message, "reasoning_content", None),
        )

    def complete_structured(
        self,
        *,
        instructions,
        input_text,
        schema_name,
        schema,
        max_output_tokens=4_096,
    ):
        response = self.client.responses.create(
            model=self.model,
            instructions=instructions,
            input=input_text,
            reasoning={"effort": "none"},
            max_output_tokens=max_output_tokens,
            text={
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": schema,
                }
            },
        )
        return ModelResponse(
            content=response.output_text,
            tool_calls=[],
            usage=self._to_responses_usage(getattr(response, "usage", None)),
        )

    def stream(self, context: ModelContext):
        messages, tools = context.to_request()
        response_stream = self.client.chat.completions.create(
            model=self.model,
            messages=self._to_openai_messages(messages),
            tools=tools,
            stream=True,
            stream_options={"include_usage": True},
            **self._thinking_options(),
        )
        finish_reason = None
        stream_usage = None
        reasoning_parts = []
        for chunk in response_stream:
            chunk_usage = self._to_model_usage(getattr(chunk, "usage", None))
            if chunk_usage is not None:
                stream_usage = chunk_usage
            if not chunk.choices:
                continue

            choice = chunk.choices[0]
            finish_reason = choice.finish_reason or finish_reason
            delta = choice.delta
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                reasoning_parts.append(reasoning)
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

        yield StreamEnd(finish_reason=finish_reason, usage=stream_usage,
                        reasoning_content="".join(reasoning_parts) if reasoning_parts else None)
