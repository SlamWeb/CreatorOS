from copy import deepcopy
from dataclasses import dataclass

from ..ai.context import ModelContext, estimate_tokens


MIN_KEEP_RECENT_TOKENS = 8_000
MAX_KEEP_RECENT_TOKENS = 128_000
KEEP_RECENT_INPUT_DIVISOR = 8


def _message_tokens(messages: list[dict]) -> int:
    return estimate_tokens(messages) if messages else 0


def calculate_keep_recent_tokens(input_limit: int) -> int:
    if input_limit <= 0:
        raise ValueError("input_limit 必须大于 0。")
    proportional = input_limit // KEEP_RECENT_INPUT_DIVISOR
    bounded = max(MIN_KEEP_RECENT_TOKENS, min(MAX_KEEP_RECENT_TOKENS, proportional))
    return min(input_limit, bounded)


@dataclass(frozen=True)
class CompactionPlan:
    """A read-only split of old history and recent complete user turns."""

    messages_to_summarize: tuple[dict, ...]
    retained_messages: tuple[dict, ...]
    first_retained_index: int
    estimated_retained_tokens: int
    input_limit: int
    keep_recent_tokens: int

    @classmethod
    def from_context(
        cls,
        context: ModelContext,
        *,
        input_limit: int,
        keep_recent_tokens: int | None = None,
    ) -> "CompactionPlan":
        if input_limit <= 0:
            raise ValueError("input_limit 必须大于 0。")
        if keep_recent_tokens is None:
            keep_recent_tokens = calculate_keep_recent_tokens(input_limit)
        if keep_recent_tokens <= 0:
            raise ValueError("keep_recent_tokens 必须大于 0。")
        if keep_recent_tokens > input_limit:
            raise ValueError("keep_recent_tokens 不能超过 input_limit。")

        messages = [deepcopy(message) for message in context.messages]
        turn_starts = [
            index
            for index, message in enumerate(messages)
            if message.get("role") == "user"
        ]
        if not turn_starts:
            return cls(
                messages_to_summarize=(),
                retained_messages=tuple(messages),
                first_retained_index=0,
                estimated_retained_tokens=_message_tokens(messages),
                input_limit=input_limit,
                keep_recent_tokens=keep_recent_tokens,
            )

        first_retained_index = turn_starts[-1]
        for candidate_index in reversed(turn_starts[:-1]):
            candidate = messages[candidate_index:]
            if _message_tokens(candidate) > keep_recent_tokens:
                break
            first_retained_index = candidate_index

        old_messages = messages[:first_retained_index]
        retained_messages = messages[first_retained_index:]
        return cls(
            messages_to_summarize=tuple(old_messages),
            retained_messages=tuple(retained_messages),
            first_retained_index=first_retained_index,
            estimated_retained_tokens=_message_tokens(retained_messages),
            input_limit=input_limit,
            keep_recent_tokens=keep_recent_tokens,
        )

    @property
    def can_compact(self) -> bool:
        return bool(self.messages_to_summarize)

    @property
    def retained_turn_exceeds_budget(self) -> bool:
        return self.estimated_retained_tokens > self.keep_recent_tokens
