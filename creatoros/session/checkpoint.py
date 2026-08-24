import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..ai.types import ModelUsage
from . import snapshot


def _messages_digest(messages) -> str:
    serialized = json.dumps(
        messages,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


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

    def __post_init__(self):
        summary = self.summary.strip()
        if not summary:
            raise ValueError("checkpoint summary 不能为空。")
        if not 0 <= self.first_retained_index <= self.source_message_count:
            raise ValueError("first_retained_index 超出 Session 范围。")
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
    ) -> "CompactionCheckpoint":
        source_messages = deepcopy(list(messages))
        return cls(
            summary=summary,
            first_retained_index=first_retained_index,
            source_message_count=len(source_messages),
            retained_messages=tuple(source_messages[first_retained_index:]),
            source_digest=_messages_digest(source_messages),
            tokens_before=tokens_before,
            usage=usage,
        )

    def matches_session(self, messages) -> bool:
        current_messages = list(messages)
        if len(current_messages) < self.source_message_count:
            return False
        source_messages = current_messages[: self.source_message_count]
        return _messages_digest(source_messages) == self.source_digest

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
