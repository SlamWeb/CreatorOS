import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..ai.types import ModelUsage
from . import snapshot


COMPACTION_SUMMARY_PREFIX = (
    "The conversation history before this point was compacted into the "
    "following summary:\n\n<summary>\n"
)
COMPACTION_SUMMARY_SUFFIX = "\n</summary>"
_STABLE_ROLES = {"system", "developer"}
_PINNABLE_HOST_SOURCES = {"request_context_event", "account_context_event"}


def _messages_digest(messages) -> str:
    serialized = json.dumps(
        messages,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _stable_content_independent_digest(messages) -> str:
    """Bind all source bytes except content of the leading stable messages.

    Roles, boundaries, content-key presence, all other stable-message fields,
    and every later message remain part of the digest. Current host instructions
    may therefore change without accepting edits to conversation history.
    """
    normalized = deepcopy(list(messages))
    for message in normalized:
        if not isinstance(message, dict) or message.get("role") not in _STABLE_ROLES:
            break
        if "content" in message:
            message["content"] = None
    return _messages_digest(normalized)


def checkpoint_path(session_file: Path | None = None) -> Path:
    path = Path(session_file or snapshot.SESSION_FILE)
    return path.with_suffix(".compaction.json")


@dataclass(frozen=True)
class CompactionCheckpoint:
    summary: str
    first_retained_index: int
    source_message_count: int
    retained_messages: tuple[dict, ...]
    source_digest: str
    tokens_before: int
    usage: ModelUsage | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    pinned_user_index: int | None = None
    stable_content_independent_digest: str | None = None
    pinned_host_indices: tuple[int, ...] = ()

    def __post_init__(self):
        summary = self.summary.strip()
        if not summary:
            raise ValueError("checkpoint summary 不能为空。")
        if not 0 <= self.first_retained_index <= self.source_message_count:
            raise ValueError("first_retained_index 超出 Session 范围。")
        if self.pinned_user_index is not None and not (
            0 <= self.pinned_user_index < self.first_retained_index
        ):
            raise ValueError("pinned_user_index 必须位于保留后缀之前。")
        if (not isinstance(self.pinned_host_indices, (list, tuple))
                or any(type(index) is not int for index in self.pinned_host_indices)
                or tuple(sorted(set(self.pinned_host_indices))) != tuple(self.pinned_host_indices)
                or (self.pinned_host_indices and self.pinned_user_index is None)
                or any(not self.pinned_user_index < index < self.first_retained_index
                       for index in self.pinned_host_indices)):
            raise ValueError("pinned_host_indices 必须唯一、有序且位于固定用户消息与保留后缀之间。")
        object.__setattr__(self, "pinned_host_indices", tuple(self.pinned_host_indices))
        expected_retained = self.source_message_count - self.first_retained_index
        if len(self.retained_messages) != expected_retained:
            raise ValueError("retained_messages 与切分位置不一致。")
        if not all(isinstance(message, dict) for message in self.retained_messages):
            raise ValueError("retained_messages 必须由消息对象组成。")
        if self.tokens_before < 0:
            raise ValueError("tokens_before 不能小于 0。")
        if len(self.source_digest) != 64 or any(
            character not in "0123456789abcdef" for character in self.source_digest
        ):
            raise ValueError("source_digest 格式无效。")
        if self.stable_content_independent_digest is not None and (
            len(self.stable_content_independent_digest) != 64 or any(
                character not in "0123456789abcdef"
                for character in self.stable_content_independent_digest
            )
        ):
            raise ValueError("stable_content_independent_digest 格式无效。")
        object.__setattr__(self, "summary", summary)
        object.__setattr__(
            self,
            "retained_messages",
            tuple(deepcopy(message) for message in self.retained_messages),
        )

    @classmethod
    def create(
        cls,
        *,
        summary: str,
        messages,
        first_retained_index: int,
        tokens_before: int,
        usage: ModelUsage | None = None,
        pinned_user_index: int | None = None,
        pinned_host_indices: tuple[int, ...] = (),
    ) -> "CompactionCheckpoint":
        source_messages = deepcopy(list(messages))
        if pinned_user_index is not None and source_messages[pinned_user_index].get("role") != "user":
            raise ValueError("只能固定原始用户请求。")
        if any(source_messages[index].get("role") != "user" or
               source_messages[index].get("creatoros_source") not in _PINNABLE_HOST_SOURCES
               for index in pinned_host_indices):
            raise ValueError("只能固定宿主上下文消息。")
        return cls(
            summary=summary,
            first_retained_index=first_retained_index,
            source_message_count=len(source_messages),
            retained_messages=tuple(source_messages[first_retained_index:]),
            source_digest=_messages_digest(source_messages),
            stable_content_independent_digest=_stable_content_independent_digest(source_messages),
            tokens_before=tokens_before,
            usage=usage,
            pinned_user_index=pinned_user_index,
            pinned_host_indices=pinned_host_indices,
        )

    def matches_session(self, messages) -> bool:
        current_messages = list(messages)
        if len(current_messages) < self.source_message_count:
            return False
        source_messages = current_messages[: self.source_message_count]
        if self.pinned_user_index is not None and source_messages[self.pinned_user_index].get("role") != "user":
            return False
        if any(source_messages[index].get("role") != "user" or
               source_messages[index].get("creatoros_source") not in _PINNABLE_HOST_SOURCES
               for index in self.pinned_host_indices):
            return False
        if _messages_digest(source_messages) == self.source_digest:
            return True
        # Old checkpoints have no second digest and stay strict. This is safe
        # only when every non-content source field and every history byte match.
        return (self.stable_content_independent_digest is not None and
                _stable_content_independent_digest(source_messages) ==
                self.stable_content_independent_digest)

    def project_messages(self, messages) -> list[dict]:
        current_messages = deepcopy(list(messages))
        if not self.matches_session(current_messages):
            raise ValueError("checkpoint 与当前 Session 不匹配。")

        stable_prefix = []
        for message in current_messages:
            if message.get("role") not in _STABLE_ROLES:
                break
            stable_prefix.append(message)
        if self.first_retained_index < len(stable_prefix):
            raise ValueError("checkpoint 不能压缩稳定 system/developer 前缀。")

        summary_message = {
            "role": "user",
            "content": (
                COMPACTION_SUMMARY_PREFIX
                + self.summary
                + COMPACTION_SUMMARY_SUFFIX
            ),
        }
        appended_messages = current_messages[self.source_message_count :]
        return [
            *stable_prefix,
            summary_message,
            *([current_messages[self.pinned_user_index]] if self.pinned_user_index is not None else []),
            *(current_messages[index] for index in self.pinned_host_indices),
            *deepcopy(list(self.retained_messages)),
            *appended_messages,
        ]

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "first_retained_index": self.first_retained_index,
            "source_message_count": self.source_message_count,
            "retained_messages": deepcopy(list(self.retained_messages)),
            "source_digest": self.source_digest,
            "tokens_before": self.tokens_before,
            "usage": self.usage.to_dict() if self.usage else None,
            "created_at": self.created_at,
            "pinned_user_index": self.pinned_user_index,
            "pinned_host_indices": list(self.pinned_host_indices),
            "stable_content_independent_digest": self.stable_content_independent_digest,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CompactionCheckpoint":
        usage_data = data.get("usage")
        usage = ModelUsage(**usage_data) if usage_data else None
        return cls(
            summary=data["summary"],
            first_retained_index=data["first_retained_index"],
            source_message_count=data["source_message_count"],
            retained_messages=tuple(data["retained_messages"]),
            source_digest=data["source_digest"],
            tokens_before=data["tokens_before"],
            usage=usage,
            created_at=data["created_at"],
            pinned_user_index=data.get("pinned_user_index"),
            pinned_host_indices=tuple(data.get("pinned_host_indices", ())),
            stable_content_independent_digest=data.get("stable_content_independent_digest"),
        )


def save_compaction_checkpoint(
    checkpoint: CompactionCheckpoint,
    session_file: Path | None = None,
) -> Path:
    path = checkpoint_path(session_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = path.with_suffix(".tmp")
    temporary_file.write_text(
        json.dumps(checkpoint.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_file.replace(path)
    return path


def load_compaction_checkpoint(
    messages,
    session_file: Path | None = None,
) -> CompactionCheckpoint | None:
    path = checkpoint_path(session_file)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        checkpoint = CompactionCheckpoint.from_dict(data)
    except (
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
        OSError,
        json.JSONDecodeError,
    ):
        return None
    return checkpoint if checkpoint.matches_session(messages) else None


def clear_compaction_checkpoint(session_file: Path | None = None):
    checkpoint_path(session_file).unlink(missing_ok=True)
