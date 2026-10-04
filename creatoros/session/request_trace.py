"""Opt-in local ModelContext snapshots, separate from the small diagnostic ledger."""
from copy import deepcopy
import json
import os
from pathlib import Path
import re


_ID = re.compile(r"^[a-f0-9]{32}$")
_SENSITIVE = {"api_key", "apikey", "password", "secret", "authorization", "cookie",
              "access_token", "refresh_token"}


def redact(value):
    """Remove known environment credentials and explicit credential fields, not user data."""
    secrets = {v for k, v in os.environ.items() if len(v) >= 8 and
               (k.endswith(("_API_KEY", "_TOKEN", "_PASSWORD", "_SECRET")))}
    changed = False

    def clean(item):
        nonlocal changed
        if isinstance(item, dict):
            result = {}
            for key, child in item.items():
                if str(key).lower() in _SENSITIVE and child is not None:
                    result[key] = "[REDACTED]"
                    changed = True
                else:
                    result[key] = clean(child)
            return result
        if isinstance(item, (list, tuple)):
            return [clean(child) for child in item]
        if isinstance(item, str):
            original = item
            for secret in secrets:
                item = item.replace(secret, "[REDACTED]")
            # Arguments/results may be JSON strings rather than objects.
            try:
                parsed = json.loads(item)
            except ValueError:
                parsed = None
            if isinstance(parsed, (dict, list)):
                cleaned = clean(parsed)
                if cleaned != parsed:
                    item = json.dumps(cleaned, ensure_ascii=False)
            changed |= item != original
            return item
        return item

    return clean(deepcopy(value)), changed


class RequestSnapshots:
    def __init__(self, session_file):
        self.session_file = Path(session_file)
        self.root = self.session_file.with_suffix(".request-trace")

    def _path(self, request_id):
        if not _ID.fullmatch(request_id):
            raise ValueError("Invalid request ID")
        if self.session_file.parent.is_symlink() or self.root.is_symlink():
            raise ValueError("Linked snapshot directory")
        path = self.root / (request_id + ".json")
        if path.is_symlink():
            raise ValueError("Linked snapshot file")
        return path

    def read(self, request_id):
        path = self._path(request_id)
        # A corrupt/local oversized file is unavailable, not an arbitrary file export.
        if path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("Snapshot too large")
        doc = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or doc.get("schema_version") != 1 or doc.get("request_id") != request_id:
            raise ValueError("Invalid snapshot")
        context = doc.get("context")
        if (not isinstance(context, dict) or
                not isinstance(context.get("messages"), list) or
                not all(isinstance(message, dict) for message in context["messages"]) or
                not isinstance(context.get("tools"), list) or
                not isinstance(doc.get("tool_results"), list) or
                not all(isinstance(result, dict) for result in doc["tool_results"]) or
                (doc.get("response") is not None and not isinstance(doc["response"], dict))):
            raise ValueError("Invalid snapshot payload")
        return doc

    def write(self, request_id, doc):
        path = self._path(request_id)
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        if temporary.is_symlink():
            raise ValueError("Linked snapshot temporary file")
        clean, changed = redact(doc)
        clean["redacted"] = bool(doc.get("redacted") or changed)
        temporary.write_text(json.dumps(clean, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)

    def begin(self, request_id, turn_id, context):
        messages, tools = context.to_request()
        self.write(request_id, {"schema_version": 1, "request_id": request_id,
            "turn_id": turn_id, "context": {"messages": messages, "tools": tools,
                "max_output_tokens": context.max_output_tokens},
            "response": None, "tool_results": [], "redacted": False})

    def response(self, request_id, response):
        doc = self.read(request_id)
        doc["response"] = response.to_message()
        self.write(request_id, doc)

    def tool_result(self, request_id, call, result):
        doc = self.read(request_id)
        doc["tool_results"].append({"tool_call_id": call.id, "name": call.name,
            "content": result.to_model_content(), "is_error": result.is_error,
            "error_type": result.error_type})
        self.write(request_id, doc)
