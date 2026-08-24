import json
import math
from copy import deepcopy
from dataclasses import dataclass, replace

from .types import ModelUsage


_STABLE_ROLES = {"system", "developer"}
DEFAULT_CONTEXT_WINDOW = 32_768
DEFAULT_RESERVE_OUTPUT_TOKENS = 4_096
MAX_MODEL_TOOL_RESULT_CHARS = 16_000


def estimate_tokens(value) -> int:
    """Conservative provider-independent estimate; not a tokenizer result."""
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    ascii_chars = sum(character.isascii() for character in serialized)
    non_ascii_chars = len(serialized) - ascii_chars
    return max(1, math.ceil(ascii_chars / 4) + non_ascii_chars)


def project_tool_result_content(
    content: str,
    *,
    max_chars: int,
    result_ref: str | None,
    scope: str,
) -> tuple[str, int]:
    if max_chars <= 0:
        raise ValueError("max_chars 必须大于 0。")
    if len(content) <= max_chars:
        return content, 0

    head_chars = max_chars // 2
    tail_chars = max_chars - head_chars
    omitted = len(content) - max_chars
    marker = (
        f"\n[{scope}: {omitted} chars omitted from middle; "
        f"full result retained in session; result_ref={result_ref or 'unknown'}]\n"
    )
    return content[:head_chars] + marker + content[-tail_chars:], omitted


def project_tool_results_for_model(
    messages,
    max_tool_result_chars: int = MAX_MODEL_TOOL_RESULT_CHARS,
) -> list[dict]:
    if max_tool_result_chars <= 0:
        raise ValueError("max_tool_result_chars 必须大于 0。")

    projected = deepcopy(list(messages))
    for message in projected:
        content = message.get("content")
        if message.get("role") != "tool" or not isinstance(content, str):
            continue
        message["content"], _ = project_tool_result_content(
            content,
            max_chars=max_tool_result_chars,
            result_ref=message.get("tool_call_id"),
            scope="model-context projection",
        )
    return projected


@dataclass(frozen=True)
class ModelContext:
    """Read-only request snapshot assembled for one model turn."""

    system_messages: tuple[dict, ...]
    tools: tuple[dict, ...]
    messages: tuple[dict, ...]

    @classmethod
    def from_messages(cls, messages, tools):
        system_messages = []
        conversation = []
        in_conversation = False
        for message in messages:
            copied = deepcopy(message)
            if not in_conversation and copied.get("role") in _STABLE_ROLES:
                system_messages.append(copied)
            else:
                in_conversation = True
                conversation.append(copied)
        return cls(
            system_messages=tuple(system_messages),
            tools=tuple(deepcopy(tool) for tool in tools),
            messages=tuple(conversation),
        )

    def to_request(self) -> tuple[list[dict], list[dict]]:
        request_messages = [
            deepcopy(message)
            for message in (*self.system_messages, *self.messages)
        ]
        request_tools = [deepcopy(tool) for tool in self.tools]
        return request_messages, request_tools


@dataclass(frozen=True)
class ContextBudget:
    context_window: int
    reserve_output_tokens: int
    estimated_input_tokens: int
    actual_input_tokens: int | None = None

    def __post_init__(self):
        if self.context_window <= 0:
            raise ValueError("context_window 必须大于 0。")
        if not 0 <= self.reserve_output_tokens < self.context_window:
            raise ValueError("reserve_output_tokens 必须小于 context_window。")
        if self.estimated_input_tokens < 0:
            raise ValueError("estimated_input_tokens 不能小于 0。")
        if self.actual_input_tokens is not None and self.actual_input_tokens < 0:
            raise ValueError("actual_input_tokens 不能小于 0。")

    @classmethod
    def from_context(
        cls,
        context: ModelContext,
        context_window: int = DEFAULT_CONTEXT_WINDOW,
        reserve_output_tokens: int = DEFAULT_RESERVE_OUTPUT_TOKENS,
    ):
        messages, tools = context.to_request()
        estimated = estimate_tokens({"messages": messages, "tools": tools})
        return cls(context_window, reserve_output_tokens, estimated)

    def with_usage(self, usage: ModelUsage) -> "ContextBudget":
        return replace(self, actual_input_tokens=usage.input_tokens)

    @property
    def input_tokens(self) -> int:
        return (
            self.actual_input_tokens
            if self.actual_input_tokens is not None
            else self.estimated_input_tokens
        )

    @property
    def measurement(self) -> str:
        return "actual" if self.actual_input_tokens is not None else "estimate"

    @property
    def input_limit(self) -> int:
        return self.context_window - self.reserve_output_tokens

    @property
    def remaining_tokens(self) -> int:
        return self.input_limit - self.input_tokens

    @property
    def is_over_limit(self) -> bool:
        return self.remaining_tokens < 0

    @property
    def is_near_limit(self) -> bool:
        warning_margin = max(1, self.input_limit // 10)
        return self.remaining_tokens <= warning_margin

    @property
    def needs_attention(self) -> bool:
        return self.is_over_limit or self.is_near_limit

    def to_event_data(self) -> dict[str, int | bool | str | None]:
        return {
            "context_window": self.context_window,
            "reserve_output_tokens": self.reserve_output_tokens,
            "estimated_input_tokens": self.estimated_input_tokens,
            "actual_input_tokens": self.actual_input_tokens,
            "input_tokens": self.input_tokens,
            "measurement": self.measurement,
            "input_limit": self.input_limit,
            "remaining_tokens": self.remaining_tokens,
            "over_limit": self.is_over_limit,
        }
