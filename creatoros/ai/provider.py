from typing import Any, Iterator, Protocol

from .context import ModelContext
from .types import ModelResponse, ModelStreamEvent


class ModelProvider(Protocol):
    context_window: int | None
    reserve_output_tokens: int | None

    def complete(self, context: ModelContext) -> ModelResponse:
        ...

    def stream(self, context: ModelContext) -> Iterator[ModelStreamEvent]:
        ...


class StructuredModelProvider(Protocol):
    def complete_structured(
        self,
        *,
        instructions: str,
        input_text: str,
        schema_name: str,
        schema: dict[str, Any],
        max_output_tokens: int = 4_096,
    ) -> ModelResponse:
        ...
