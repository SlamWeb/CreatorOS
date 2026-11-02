"""Durable continuation attempt evidence, before any model output reaches tools."""
from openai import APIConnectionError

PROTOCOL = 1
MAX_ATTEMPTS = 3
RETRY_SECONDS = 30


def can_retry(record):
    return (record.get("continuation_protocol") == PROTOCOL
            and record.get("continuation_stage") in {"prepared", "model_started"}
            and record.get("continuation_attempts", MAX_ATTEMPTS) < MAX_ATTEMPTS)


def settled_outcome(record, status):
    if status == "idle":
        return "done"
    if status == "failed" and record.get("continuation_transient_failure") and can_retry(record):
        return "retry_pending"
    if record.get("continuation_stage") == "output_seen" or status == "interrupted":
        return "unknown"
    return "failed"


class ContinuationProvider:
    """Delegate the provider, persisting a fail-closed gate before yielding output.

    A lost first response can cost remote model tokens but cannot have executed
    a host tool. Once output is exposed, automatic replay is forbidden.
    """
    def __init__(self, provider, record, update):
        self.provider, self.record, self.update = provider, record, update

    def __getattr__(self, name):
        return getattr(self.provider, name)

    def before(self):
        fields = {"continuation_model_calls": self.record.get("continuation_model_calls", 0) + 1}
        if self.record.get("continuation_stage") != "output_seen":
            fields["continuation_stage"] = "model_started"
        self.update(**fields)

    def output(self):
        if self.record.get("continuation_stage") != "output_seen":
            self.update(continuation_stage="output_seen")

    def failed(self):
        if self.record.get("continuation_stage") == "model_started" and self.record["continuation_model_calls"] == 1:
            self.update(continuation_transient_failure=True)

    def stream(self, context):
        self.before()
        try:
            for event in self.provider.stream(context):
                self.output()
                yield event
        except (APIConnectionError, ConnectionError, TimeoutError):
            self.failed()
            raise

    def complete(self, context):
        self.before()
        try:
            response = self.provider.complete(context)
        except (APIConnectionError, ConnectionError, TimeoutError):
            self.failed()
            raise
        self.output()
        return response
