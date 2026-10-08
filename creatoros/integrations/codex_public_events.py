"""Best-effort public SDK evidence, separate from business state and progress."""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

FILENAME = "codex_public_events.jsonl"
MAX_RECORD_BYTES = 8 * 1024 * 1024
LOG = logging.getLogger(__name__)
PUBLIC_ITEMS = {
    "userMessage", "agentMessage", "functionCallOutput", "plan", "commandExecution",
    "fileChange", "mcpToolCall", "dynamicToolCall", "collabAgentToolCall", "collabToolCall",
    "subAgentActivity", "webSearch", "imageView", "sleep", "imageGeneration",
    "enteredReviewMode", "exitedReviewMode", "contextCompaction",
}
PRIVATE_KEYS = {"reasoning", "reasoningcontent", "reasoningtext", "encryptedcontent",
                "encryptedreasoning", "chainofthought"}
SECRET_KEYS = {"apikey", "password", "secret", "authorization", "cookie", "setcookie",
               "token", "accesstoken", "refreshtoken", "idtoken", "privatekey", "headers"}
BINARY_KEYS = {"blob", "base64", "b64json", "imagebase64", "audiobase64", "imagedata", "audiodata"}
TEXT_DELTAS = {"item/agentMessage/delta": ("agentMessage", "text"),
               "item/plan/delta": ("plan", "text"),
               "item/commandExecution/outputDelta": ("commandExecution", "aggregated_output"),
               "item/fileChange/outputDelta": ("fileChange", "output")}


def field(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _key(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def sanitize_public(value):
    """Return (clean, redacted, omissions), never serialize reasoning or binary bodies.

    Ordinary public strings/lists are complete. Depth is bounded with an explicit
    omission. JSON encoded tool arguments/results are checked recursively too.
    """
    omissions = []
    redacted = False
    secrets = {v for k, v in os.environ.items() if len(v) >= 8 and
               k.upper().endswith(("_API_KEY", "_TOKEN", "_PASSWORD", "_SECRET"))}

    def omit(path, reason):
        omissions.append({"path": path, "reason": reason})
        return "[OMITTED]"

    def clean(item, path="$", depth=0):
        nonlocal redacted
        if depth > 64:
            return omit(path, "maximum_nesting_depth")
        # Ordinary tool JSON may legitimately have a field named root.
        item = field(item, "root", item) if not isinstance(item, dict) else item
        if isinstance(item, Enum):
            return clean(item.value, path, depth + 1)
        if _key(field(item, "type", "")) in {"reasoning", "reasoningtext", "summarytext"}:
            omit(path, "reasoning_body_excluded")
            return {k: clean(field(item, k), path + "." + k, depth + 1)
                    for k in ("id", "type") if field(item, k) is not None}
        if hasattr(type(item), "model_fields"):
            # Walk individual fields; model_dump would serialize reasoning first.
            item = {k: getattr(item, k) for k in type(item).model_fields}
        if isinstance(item, dict):
            result = {}
            kind = _key(item.get("type", ""))
            for key, child in item.items():
                label, normalized = str(key), _key(key)
                location = path + "." + label
                if normalized in PRIVATE_KEYS:
                    result[label] = omit(location, "reasoning_or_encrypted_content_excluded")
                elif normalized in SECRET_KEYS:
                    result[label] = "[REDACTED]" if child is not None else None
                    if child is not None:
                        redacted = True
                        omissions.append({"path": location, "reason": "credential_field_redacted"})
                elif normalized in BINARY_KEYS or (normalized in {"data", "imageurl", "audiourl", "url"}
                        and kind in {"image", "inputimage", "audio", "inputaudio"}) or (
                        kind == "imagegeneration" and normalized == "result"):
                    result[label] = omit(location, "binary_media_excluded") if child else child
                else:
                    result[label] = clean(child, location, depth + 1)
            return result
        if isinstance(item, (list, tuple)):
            return [clean(child, f"{path}[{i}]", depth + 1) for i, child in enumerate(item)]
        if isinstance(item, str):
            original = item
            try:
                parsed = json.loads(item) if item.lstrip().startswith(("{", "[")) else None
            except (ValueError, RecursionError):
                parsed = None
            if isinstance(parsed, (dict, list)):
                checked = clean(parsed, path + ".json", depth + 1)
                return json.dumps(checked, ensure_ascii=False) if checked != parsed else item
            for secret in secrets:
                item = item.replace(secret, "[REDACTED]")
            item = re.sub(r"(?i)\bBearer\s+[^\s\"']+", "Bearer [REDACTED]", item)
            item = re.sub(r"(?im)(\b(?:[A-Z0-9_]*API[_-]?KEY|[A-Z0-9_]*TOKEN|PASSWORD|SECRET|"
                          r"AUTHORIZATION|COOKIE)\b[\"']?\s*[:=]\s*)"
                          r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;}]+)",
                          r"\1[REDACTED]", item)
            item = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-]*PRIVATE KEY-----|$)",
                          "[REDACTED PRIVATE KEY]", item)
            if item != original:
                redacted = True
                omissions.append({"path": path, "reason": "credential_or_private_content_redacted"})
            media = re.sub(r"data:[^\s,;]+;base64,[A-Za-z0-9+/=]+", "[OMITTED MEDIA]", item)
            if media != item:
                omissions.append({"path": path, "reason": "inline_binary_media_excluded"})
            return media
        if item is None or isinstance(item, (bool, int, float)):
            return item
        return omit(path, "unsupported_public_value")

    result = clean(value)
    return result, redacted, omissions


class PublicEventCapture:
    """One instance per turn. Errors here must not change the SDK result."""

    def __init__(self, directory, turn_id, phase, thread_id=""):
        self.path = Path(directory) / FILENAME
        self.turn_id, self.phase, self.thread_id = turn_id, phase, thread_id
        self.pending = {}
        self.failed = False

    def _append(self, record):
        # Avoid following a model-created link into a different task/directory.
        for path in (self.path, self.path.parent):
            if path.is_symlink() or (path.exists() and getattr(path.lstat(), "st_file_attributes", 0) & 0x400):
                raise OSError("linked public trace")
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def write(self, method, payload, *, item_id="", item_type="", status="", omissions=(), incomplete=False,
              truncated=False):
        try:
            clean, redacted, excluded = sanitize_public(payload)
            record = {"schema_version": 1, "at": datetime.now(timezone.utc).isoformat(),
                      "phase": self.phase, "method": method, "thread_id": self.thread_id,
                      "turn_id": self.turn_id, "item_id": item_id, "item_type": item_type,
                      "status": status, "payload": clean, "redacted": redacted,
                      "omissions": [*omissions, *excluded], "truncated": truncated,
                      "incomplete": incomplete}
            size = len(json.dumps(record, ensure_ascii=False).encode("utf-8"))
            if size > MAX_RECORD_BYTES:
                record.update(payload={}, truncated=True)
                record["omissions"] = [{"path": "$.payload", "reason": "payload_too_large",
                                        "original_bytes": size, "limit_bytes": MAX_RECORD_BYTES}]
            self._append(record)
        except Exception as error:
            # No exception text: arbitrary filesystem/model data can contain secrets.
            if not self.failed:
                LOG.warning("Public Codex capture unavailable (%s); SDK execution continues", type(error).__name__)
            self.failed = True

    def observe(self, event):
        try:
            self._observe(event)
        except Exception as error:
            self.write("capture/omitted", {"method": event.method, "error_type": type(error).__name__},
                       omissions=[{"path": "$.payload", "reason": "capture_projection_failed"}])

    def _observe(self, event):
        method, payload = event.method, event.payload
        self.thread_id = field(payload, "thread_id") or self.thread_id
        if method in TEXT_DELTAS:
            kind, key = TEXT_DELTAS[method]
            item_id = field(payload, "item_id", "")
            entry = self.pending.setdefault(item_id, {"type": kind, "id": item_id, key: ""})
            text = entry.get(key, "") + str(field(payload, "delta", ""))
            if len(text.encode("utf-8")) > MAX_RECORD_BYTES:
                entry.update({key: "", "capture_overflow": True})
            elif not entry.get("capture_overflow"):
                entry[key] = text
            return  # Coalesce before redaction: credentials may cross chunk boundaries.
        if method in {"item/started", "item/completed"}:
            item = field(payload, "item")
            item = field(item, "root", item) if not isinstance(item, dict) else item
            kind, item_id = field(item, "type", ""), field(item, "id", "")
            status = field(item, "status", "")
            status = field(status, "value", status) or ("completed" if method == "item/completed" else "running")
            if field(item, "error") is not None or field(item, "success") is False or field(item, "exit_code") not in {0, None}:
                status = "failed"
            omitted = []
            if kind not in PUBLIC_ITEMS | {"reasoning"}:
                item = {"id": item_id, "type": kind}
                omitted = [{"path": "$.item", "reason": "unrecognized_item_body_excluded"}]
            self.write(method, {"item": item}, item_id=item_id, item_type=kind,
                       status=status, omissions=omitted)
            if method == "item/completed":
                self.pending.pop(item_id, None)
        elif method.startswith("item/reasoning/"):
            self.write(method, {}, item_id=field(payload, "item_id", ""), item_type="reasoning",
                       omissions=[{"path": "$.payload", "reason": "reasoning_body_excluded"}])
        elif method in {"turn/started", "turn/completed"}:
            turn = field(payload, "turn", {})
            status = field(field(turn, "status", ""), "value", field(turn, "status", ""))
            self.write(method, {"turn": {"id": field(turn, "id"), "status": status,
                                        "error": field(turn, "error")}}, status=status)
        elif method == "thread/tokenUsage/updated":
            self.write(method, {"token_usage": field(payload, "token_usage")})
        elif method == "error":
            self.write(method, {"error": field(payload, "error")}, status="failed")
        elif method in {"turn/plan/updated", "turn/diff/updated", "item/mcpToolCall/progress"}:
            self.write(method, payload, item_id=field(payload, "item_id", ""))
        else:
            self.write(method, {}, item_id=field(payload, "item_id", ""),
                       omissions=[{"path": "$.payload", "reason": "unrecognized_notification_body_excluded"}])

    def finish(self, status):
        try:
            for item_id, original in self.pending.items():
                item = dict(original)
                overflow = item.pop("capture_overflow", False)
                self.write("item/partial", {"item": item}, item_id=item_id, item_type=item.get("type", ""),
                           status=status, incomplete=True, truncated=overflow,
                           omissions=[{"path": "$.item", "reason": "partial_text_too_large" if overflow
                                       else "turn_ended_before_authoritative_item_completed"}])
            self.pending.clear()
        except Exception as error:
            self.failed = True
            self.write("capture/omitted", {"error_type": type(error).__name__},
                       omissions=[{"path": "$.pending", "reason": "partial_capture_failed"}])
        self.write("capture/finished", {"capture_write_failed": self.failed,
                   "delta_policy": "completed_item_authoritative; unfinished_deltas_coalesced_at_turn_end"}, status=status)
