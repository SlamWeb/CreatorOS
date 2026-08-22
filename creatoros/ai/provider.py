from typing import Iterator, Protocol

from .types import ModelResponse, ModelStreamEvent


class ModelProvider(Protocol):
    def complete(self, messages: list[dict], tools: list[dict]) -> ModelResponse:
        ...

    def stream(
        self, messages: list[dict], tools: list[dict]
    ) -> Iterator[ModelStreamEvent]:
        ...
