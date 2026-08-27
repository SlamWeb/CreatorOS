from dataclasses import dataclass, field

from .task_state import TaskRecord


@dataclass
class AgentState:
    messages: list[dict]
    status: str = "idle"
    turn: int = 0
    tasks: dict[str, TaskRecord] = field(default_factory=dict)

    def register_remote_task(
        self,
        *,
        task_id: str,
        kind: str,
        remote_status: str,
        progress: str | None = None,
        error: str | None = None,
    ) -> TaskRecord:
        record = self.tasks.get(task_id)
        if record is None:
            record = TaskRecord(task_id=task_id, kind=kind)
            self.tasks[task_id] = record
        record.sync_remote_status(
            remote_status,
            progress=progress,
            error=error,
            result_ref=task_id,
        )
        return record
