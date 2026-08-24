from copy import deepcopy
from dataclasses import dataclass

from ..ai.context import ModelContext, estimate_tokens


DEFAULT_KEEP_RECENT_TOKENS = 20_000


def _message_tokens(messages: list[dict]) -> int:
    return estimate_tokens(messages) if messages else 0


@dataclass(frozen=True)
class CompactionPlan:
    """A read-only split of old history and recent complete user turns."""

    messages_to_summarize: tuple[dict, ...]
    retained_messages: tuple[dict, ...]
    first_retained_index: int
    estimated_retained_tokens: int
    keep_recent_tokens: int

    @classmethod
    def from_context(
        cls,
        context: ModelContext,
        keep_recent_tokens: int = DEFAULT_KEEP_RECENT_TOKENS,
    ) -> "CompactionPlan":
        if keep_recent_tokens <= 0:
            raise ValueError("keep_recent_tokens 必须大于 0。")

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
            keep_recent_tokens=keep_recent_tokens,
        )

    @property
    def can_compact(self) -> bool:
        return bool(self.messages_to_summarize)

    @property
    def retained_turn_exceeds_budget(self) -> bool:
        return self.estimated_retained_tokens > self.keep_recent_tokens
