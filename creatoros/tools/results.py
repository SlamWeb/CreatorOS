from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """Normalized result returned by every tool execution."""

    content: str
    is_error: bool = False
    error_type: str | None = None
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.content
