"""Atomic, redacted evidence writes shared by the benchmark host and CLI."""
from datetime import datetime, timezone
import json
from time import sleep
from uuid import uuid4

from creatoros.session.request_trace import redact


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(redact(value)[0], ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        for attempt in range(5):
            try:
                temporary.replace(path)
                break
            except PermissionError as error:
                if getattr(error, "winerror", None) not in {5, 32, 33} or attempt == 4:
                    raise
                sleep(0.01 * (2 ** attempt))
    finally:
        temporary.unlink(missing_ok=True)
