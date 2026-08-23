from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"

    @property
    def is_terminal(self) -> bool:
        return self in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.TIMED_OUT,
        }


class TaskHealth(str, Enum):
    HEALTHY = "healthy"
    STALLED = "stalled"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    TERMINAL = "terminal"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class TaskRecord:
    """Internal state for one external or background task.

    ``STALLED`` means that no progress was observed within the configured
    heartbeat window; it is a suspicion, not proof that the worker crashed.
    """

    task_id: str
    kind: str
    created_at: datetime = field(default_factory=utc_now)
    deadline_at: datetime | None = None
    status: TaskStatus = TaskStatus.QUEUED
    updated_at: datetime | None = None
    started_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    progress: str | None = None
    result_ref: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.updated_at is None:
            self.updated_at = self.created_at

    def mark_running(self, *, now: datetime | None = None) -> None:
        self._ensure_active()
        current = now or utc_now()
        self.status = TaskStatus.RUNNING
        self.started_at = self.started_at or current
        self.last_heartbeat_at = current
        self.updated_at = current

    def heartbeat(self, progress: str | None = None, *, now: datetime | None = None) -> None:
        if self.status is not TaskStatus.RUNNING:
            raise ValueError("只有 running 任务可以更新 heartbeat。")
        current = now or utc_now()
        self.last_heartbeat_at = current
        self.updated_at = current
        if progress is not None:
            self.progress = progress

    def complete(self, result_ref: str | None = None, *, now: datetime | None = None) -> None:
        self._ensure_active()
        current = now or utc_now()
        self.status = TaskStatus.COMPLETED
        self.result_ref = result_ref
        self.updated_at = current

    def fail(self, error: str, *, now: datetime | None = None) -> None:
        self._ensure_active()
        current = now or utc_now()
        self.status = TaskStatus.FAILED
        self.error = error
        self.updated_at = current

    def cancel(self, *, now: datetime | None = None) -> None:
        self._ensure_active()
        current = now or utc_now()
        self.status = TaskStatus.CANCELLED
        self.updated_at = current

    def timeout(self, *, now: datetime | None = None) -> None:
        self._ensure_active()
        current = now or utc_now()
        self.status = TaskStatus.TIMED_OUT
        self.error = self.error or "任务超过允许的最大运行时间。"
        self.updated_at = current

    def health(
        self,
        *,
        now: datetime | None = None,
        heartbeat_timeout: timedelta = timedelta(seconds=60),
    ) -> TaskHealth:
        current = now or utc_now()
        if self.status.is_terminal:
            return TaskHealth.TERMINAL
        if self.deadline_at is not None and current >= self.deadline_at:
            return TaskHealth.DEADLINE_EXCEEDED
        if self.status is TaskStatus.QUEUED:
            return TaskHealth.HEALTHY
        if self.last_heartbeat_at is None:
            return TaskHealth.STALLED
        if current - self.last_heartbeat_at > heartbeat_timeout:
            return TaskHealth.STALLED
        return TaskHealth.HEALTHY

    def _ensure_active(self) -> None:
        if self.status.is_terminal:
            raise ValueError(f"任务已经结束，不能从 {self.status.value} 继续变更。")
