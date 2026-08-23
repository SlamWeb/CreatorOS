from typing import Iterator, Protocol

from .context import ModelContext
from .types import ModelResponse, ModelStreamEvent


class ModelProvider(Protocol):
    context_window: int | None
    reserve_output_tokens: int | None

    def complete(self, context: ModelContext) -> ModelResponse:
        ...

    def stream(self, context: ModelContext) -> Iterator[ModelStreamEvent]:
        ...
