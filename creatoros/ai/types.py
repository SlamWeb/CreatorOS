from dataclasses import dataclass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class ModelResponse:
    content: str | None
    tool_calls: list[ToolCall]

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


@dataclass
class ToolCallEnd:
    index: int
    tool_call: ToolCall


ModelStreamEvent = TextDelta | ToolCallDelta | StreamEnd
RuntimeStreamEvent = ModelStreamEvent | ToolCallEnd
