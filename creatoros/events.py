from dataclasses import dataclass


@dataclass(frozen=True)
class AgentEvent:
    """A small semantic signal emitted while one agent task is running."""

    kind: str
    data: dict[str, object]
