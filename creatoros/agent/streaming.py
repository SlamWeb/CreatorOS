from typing import Callable

from ..ai.provider import ModelProvider
from ..ai.types import (
    ModelResponse,
    RuntimeStreamEvent,
    StreamEnd,
    TextDelta,
    ToolCall,
    ToolCallDelta,
    ToolCallEnd,
)
from ..terminal import Console


def stream_llm(
    provider: ModelProvider,
    messages: list[dict],
    tools: list[dict],
    on_event: Callable[[RuntimeStreamEvent], None] | None = None,
    console: Console | None = None,
) -> ModelResponse:
    console = console or Console()
    text_parts = []
    tool_calls_by_index = {}
    stream_end = None

    for event in provider.stream(messages=messages, tools=tools):
        if isinstance(event, StreamEnd):
            stream_end = event
            continue

        if on_event is not None:
            on_event(event)

        if isinstance(event, TextDelta):
            console.write(event.content, end="", flush=True)
            text_parts.append(event.content)
            continue

        if isinstance(event, ToolCallDelta):
            tool_call = tool_calls_by_index.setdefault(
                event.index,
                ToolCall(id=event.id or "", name=event.name or "", arguments=""),
            )
            if event.id:
                tool_call.id = event.id
            if event.name:
                tool_call.name = event.name
            tool_call.arguments += event.arguments

    console.write()
    for index in sorted(tool_calls_by_index):
        if on_event is not None:
            on_event(
                ToolCallEnd(
                    index=index,
                    tool_call=tool_calls_by_index[index],
                )
            )
    if on_event is not None and stream_end is not None:
        on_event(stream_end)

    return ModelResponse(
        content="".join(text_parts) or None,
        tool_calls=[tool_calls_by_index[index] for index in sorted(tool_calls_by_index)],
    )
