from dataclasses import dataclass, field

from .task_state import TaskRecord


@dataclass
class AgentState:
    messages: list[dict]
    status: str = "idle"
    turn: int = 0
    tasks: dict[str, TaskRecord] = field(default_factory=dict)
