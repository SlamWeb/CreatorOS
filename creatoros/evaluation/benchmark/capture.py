"""Measure real Provider calls without changing inputs, tools, or retries."""
from copy import deepcopy
from datetime import datetime, timezone
from time import monotonic
from uuid import uuid4

from creatoros.ai.context import estimate_tokens
from creatoros.ai.deepseek import DeepSeekProvider
from creatoros.ai.types import StreamEnd, TextDelta, ToolCallDelta


def text_estimation_view(value):
    """Image bytes are not text tokens; vendor usage remains authoritative."""
    if isinstance(value, str) and value.startswith("data:image/"):
        return "<image payload; visual token count unknown>"
    if isinstance(value, dict):
        return {key: text_estimation_view(item) for key, item in value.items()}
    if isinstance(value, list):
        return [text_estimation_view(item) for item in value]
    return value


class MeasuredProvider(DeepSeekProvider):
    def __init__(self, *args, sink, purpose="agent", **kwargs):
        super().__init__(*args, **kwargs)
        self.sink, self.purpose = sink, purpose

    def _record(self, context, method):
        messages, tools = context.to_request()
        return {"request_id": uuid4().hex, "purpose": self.purpose, "model": self.model,
            "started_at": datetime.now(timezone.utc).isoformat(), "method": method,
            "context": {"messages": deepcopy(messages), "tools": deepcopy(tools)},
            "context_estimated_tokens": estimate_tokens(text_estimation_view({"messages": messages, "tools": tools})),
            "context_estimate_scope": "text/schema only; excludes image encoding and unknown visual tokens",
            "tool_schema_tokens": estimate_tokens(tools), "input_tokens": None, "output_tokens": None,
            "cache_hit_tokens": None, "cache_miss_tokens": None, "ttft_ms": None,
            "first_tool_ms": None, "elapsed_seconds": None, "error": None}

    def stream(self, context):
        record = self._record(context, "stream")
        self.sink(record)
        started = monotonic()
        try:
            for event in super().stream(context):
                if isinstance(event, TextDelta) and event.content and record["ttft_ms"] is None:
                    record["ttft_ms"] = (monotonic() - started) * 1000
                if isinstance(event, ToolCallDelta) and (event.name or event.arguments) and record["first_tool_ms"] is None:
                    record["first_tool_ms"] = (monotonic() - started) * 1000
                if isinstance(event, StreamEnd) and event.usage:
                    record.update(event.usage.to_dict())
                yield event
        except Exception as error:
            record["error"] = {"kind": type(error).__name__, "message": str(error)}
            raise
        finally:
            record["elapsed_seconds"] = monotonic() - started
            self.sink(record)

    def complete(self, context):
        record = self._record(context, "complete")
        self.sink(record)
        started = monotonic()
        try:
            response = super().complete(context)
            if response.usage:
                record.update(response.usage.to_dict())
            record["response"] = {"content": response.content,
                "tool_calls": [{"name": call.name, "arguments": call.arguments} for call in response.tool_calls]}
            return response
        except Exception as error:
            record["error"] = {"kind": type(error).__name__, "message": str(error)}
            raise
        finally:
            record["elapsed_seconds"] = monotonic() - started
            self.sink(record)
