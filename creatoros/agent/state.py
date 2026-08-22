from dataclasses import dataclass


@dataclass
class AgentState:
    messages: list[dict]
    status: str = "idle"
    turn: int = 0
