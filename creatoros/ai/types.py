from dataclasses import dataclass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ModelUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cache_hit_tokens: int | None = None
    cache_miss_tokens: int | None = None

    def to_dict(self) -> dict[str, int]:
        data = {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }
        if self.cache_hit_tokens is not None:
            data["cache_hit_tokens"] = self.cache_hit_tokens
        if self.cache_miss_tokens is not None:
            data["cache_miss_tokens"] = self.cache_miss_tokens
        return data


@dataclass
class ModelResponse:
    content: str | None
    tool_calls: list[ToolCall]
    usage: ModelUsage | None = None

    def to_message(self):
        message = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [
                {"id": call.id, "name": call.name, "arguments": call.arguments}
                for call in self.tool_calls
            ]
        return message


@dataclass
class TextDelta:
    content: str


@dataclass
class ToolCallDelta:
    index: int
    id: str | None
    name: str | None
    arguments: str


@dataclass
class StreamEnd:
    finish_reason: str | None
    usage: ModelUsage | None = None


@dataclass
class ToolCallEnd:
    index: int
    tool_call: ToolCall


ModelStreamEvent = TextDelta | ToolCallDelta | StreamEnd
RuntimeStreamEvent = ModelStreamEvent | ToolCallEnd
