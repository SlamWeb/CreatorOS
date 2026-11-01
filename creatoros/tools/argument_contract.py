"""Use one discriminated union for both model schema and host validation."""
from typing import Any

from pydantic import BaseModel, TypeAdapter


class ActionContract:
    """The small args_model interface used by Tool, backed by one TypeAdapter."""

    def __init__(self, argument_type: Any):
        self._adapter = TypeAdapter(argument_type)

    def model_json_schema(self) -> dict:
        schema = self._adapter.json_schema()
        # Function providers require an object root even when branches use oneOf.
        return {"type": "object", **schema}

    def model_validate(self, value: Any) -> BaseModel:
        return self._adapter.validate_python(value)

    def model_validate_json(self, value: str | bytes | bytearray) -> BaseModel:
        return self._adapter.validate_json(value)
