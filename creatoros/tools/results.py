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
    model_content: str | None = None

    def __str__(self) -> str:
        return self.content

    def to_model_content(self) -> str:
        content = self.model_content if self.model_content is not None else self.content
        return self._with_error(content)

    def to_raw_content(self) -> str:
        return self._with_error(self.content)

    def _with_error(self, content: str) -> str:
        if not self.is_error:
            return content

        error_type = self.error_type or "unknown_error"
        return f"[tool_error type={error_type}]\n{content}"
