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


def _step_cuts(messages: list[dict], user_index: int) -> list[int]:
    """Boundaries after complete steps, never within a tool-call batch."""
    cuts = []
    index = user_index + 1
    while index < len(messages):
        assistant = messages[index]
        if assistant.get("role") != "assistant":
            break
        calls = assistant.get("tool_calls") or []
        ids = [call.get("id") for call in calls]
        if any(not value for value in ids) or len(set(ids)) != len(ids):
            break
        pending = set(ids)
        index += 1
        while pending and index < len(messages):
            result = messages[index]
            if result.get("role") != "tool" or result.get("tool_call_id") not in pending:
                return cuts
            pending.remove(result["tool_call_id"])
            index += 1
        if pending or index == len(messages):
            break
        if messages[index].get("role") != "assistant":
            break
        cuts.append(index)
    return cuts


@dataclass(frozen=True)
class CompactionPlan:
    """Prefer whole user turns; split oversized latest turns at safe steps."""

    messages_to_summarize: tuple[dict, ...]
    retained_messages: tuple[dict, ...]
    first_retained_index: int
    estimated_retained_tokens: int
    input_limit: int
    keep_recent_tokens: int
    pinned_user_index: int | None = None

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

        pinned_user_index = None
        latest_user_index = turn_starts[-1]
        if _message_tokens(messages[latest_user_index:]) > keep_recent_tokens:
            # Try the largest safe recent suffix first; retain at least one step.
            for candidate_index in _step_cuts(messages, latest_user_index):
                first_retained_index = candidate_index
                pinned_user_index = latest_user_index
                candidate = [messages[latest_user_index], *messages[candidate_index:]]
                if _message_tokens(candidate) <= keep_recent_tokens:
                    break

        old_messages = messages[:first_retained_index]
        retained_messages = messages[first_retained_index:]
        projected = ([messages[pinned_user_index]] if pinned_user_index is not None else [])
        projected.extend(retained_messages)
        return cls(
            messages_to_summarize=tuple(old_messages),
            retained_messages=tuple(retained_messages),
            first_retained_index=first_retained_index,
            estimated_retained_tokens=_message_tokens(projected),
            input_limit=input_limit,
            keep_recent_tokens=keep_recent_tokens,
            pinned_user_index=pinned_user_index,
        )

    @property
    def can_compact(self) -> bool:
        return bool(self.messages_to_summarize)

    @property
    def retained_turn_exceeds_budget(self) -> bool:
        return self.estimated_retained_tokens > self.keep_recent_tokens
