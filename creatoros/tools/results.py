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

    def to_model_content(self) -> str:
        if not self.is_error:
            return self.content

        error_type = self.error_type or "unknown_error"
        return f"[tool_error type={error_type}]\n{self.content}"
