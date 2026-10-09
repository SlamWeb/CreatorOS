"""Opt-in public Codex activity. Never serialize reasoning or the whole notification."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

from ..session.request_trace import redact
from .atomic_file import atomic_write_text


def _write(path, value):
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2))

MAX_TEXT = 64 * 1024
PREVIEW = 500
MAX_EVENTS = 2000
PUBLIC_TOOLS = {"commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall", "webSearch"}


def field(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def safe_text(value):
    clean, changed = redact(value)
    text = clean if isinstance(clean, str) else json.dumps(clean, ensure_ascii=False, default=str)
    # Credentials can also appear in plain shell output, not just JSON fields.
    filtered = re.sub(r"(?im)\b(authorization|cookie|api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)"
                      r"(\s*[:=]\s*)([^\r\n,}]+)", r"\1\2[REDACTED]", text)
    filtered = re.sub(r"(?i)\bBearer\s+[^\s\"']+", "Bearer [REDACTED]", filtered)
    return filtered[:MAX_TEXT], changed or filtered != text, len(filtered) > MAX_TEXT


def _text_content(contents):
    # MCP image/base64 and encrypted content must not enter public activity.
    return "\n".join(str(field(item, "text", ""))[:MAX_TEXT] for item in (contents or [])[:40]
                     if field(item, "type") in {"text", "input_text"})[:MAX_TEXT]


def _public_value(value, depth=0):
    if depth > 5:
        return "[nested content omitted]"
    if isinstance(value, dict):
        return {str(key): _public_value(child, depth + 1) for key, child in list(value.items())[:40]
                if str(key).lower() not in {"headers", "data", "blob", "image_url", "encrypted_content", "reasoning"}}
    if isinstance(value, (list, tuple)):
        return [_public_value(child, depth + 1) for child in value[:40]]
    if isinstance(value, str):
        return value[:MAX_TEXT]
    return value


class ExtractionActivity:
    def __init__(self, directory: Path):
        self.root = directory / "public_events"
        self.root.mkdir(exist_ok=True)
        self.entries = []
        self.documents = {}
        self.last_write = {}
        index = self.root / "index.json"
        if index.exists():
            self.entries = json.loads(index.read_text(encoding="utf-8"))["items"]
        self.keys = {entry["item_id"]: entry["id"] for entry in self.entries}

    def put(self, key, kind, title, status, text, *, item_type="", force=True, already_redacted=False):
        if key not in self.keys:
            if len(self.entries) >= MAX_EVENTS:
                return
            self.keys[key] = len(self.entries) + 1
            at = datetime.now(timezone.utc).isoformat()
            self.entries.append({"id": self.keys[key], "at": at, "item_id": key})
        identifier = self.keys[key]
        entry = self.entries[identifier - 1]
        clean, redacted, truncated = safe_text(text)
        document = {**entry, "kind": kind, "title": title[:120], "status": status,
                    "updated_at": datetime.now(timezone.utc).isoformat(), "text": clean,
                    "redacted": redacted or already_redacted, "truncated": truncated, "item_type": item_type}
        self.documents[key] = document
        if not force and monotonic() - self.last_write.get(key, 0) < 0.5:
            return
        self._save(key)

    def _save(self, key):
        document = self.documents[key]
        entry = {**document, "text": document["text"][:PREVIEW],
                 "truncated": document["truncated"] or len(document["text"]) > PREVIEW}
        # Detail first: index never points at a not-yet-written public document.
        _write(self.root / f"{document['id']}.json", document)
        self.entries[document["id"] - 1] = entry
        _write(self.root / "index.json", {"items": self.entries, "limit_reached": len(self.entries) >= MAX_EVENTS})
        self.last_write[key] = monotonic()

    def observe(self, event):
        method, payload = event.method, event.payload
        if method in {"item/started", "item/completed"}:
            item = field(payload, "item")
            item = field(item, "root", item)
            kind = field(item, "type", "")
            key = str(field(item, "id", ""))
            if not key or kind not in PUBLIC_TOOLS | {"agentMessage", "plan"}:
                return
            status = "running" if method == "item/started" else "completed"
            reported = field(item, "status", "")
            reported = field(reported, "value", reported)
            if reported in {"failed", "declined"} or field(item, "exit_code", 0) not in {0, None}:
                status = "failed"
            if kind in {"agentMessage", "plan"}:
                self.put(key, "message", "Codex" if kind == "agentMessage" else "计划", status,
                         field(item, "text", ""), item_type=kind)
                return
            if kind == "commandExecution":
                title = "执行命令"
                empty = "工具已结束，没有文本输出" if method == "item/completed" else "尚未返回输出"
                text = f"命令：\n{field(item, 'command', '')}\n\n结果：\n{field(item, 'aggregated_output') or empty}"
                if field(item, "exit_code") is not None:
                    text += f"\n\n退出码：{field(item, 'exit_code')}"
            elif kind == "fileChange":
                title = "修改文件"
                text = "\n".join(str(field(change, "path", "")) + "\n" + str(field(change, "diff", ""))[:MAX_TEXT]
                                 for change in field(item, "changes", []))[:MAX_TEXT]
            elif kind in {"mcpToolCall", "dynamicToolCall"}:
                title = f"{field(item, 'server') or field(item, 'namespace') or '工具'}.{field(item, 'tool', '')}"
                arguments, argument_redacted, _ = safe_text(_public_value(field(item, "arguments", {})))
                result = field(item, "result")
                output = _text_content(field(result, "content", []) if result else field(item, "content_items", []))
                structured = field(result, "structured_content")
                output_redacted = False
                if structured:
                    structured_text, output_redacted, _ = safe_text(_public_value(structured))
                    output += "\n" + structured_text
                error = field(item, "error")
                if error:
                    status = "failed"
                    output += "\n" + str(field(error, "message", "工具返回错误"))
                empty = "工具已结束，没有可展示文本结果" if method == "item/completed" else "尚未返回可见文本结果"
                text = f"输入：\n{arguments}\n\n结果：\n{output or empty}"
            else:
                title, text = "搜索", str(field(item, "query", ""))
            self.put(key, "tool", title, status, text, item_type=kind,
                     already_redacted=(argument_redacted or output_redacted) if kind in {"mcpToolCall", "dynamicToolCall"} else False)
        elif method == "item/agentMessage/delta":
            key = str(field(payload, "item_id", ""))
            if not key:
                return
            previous = self.documents.get(key, {}).get("text", "")
            self.put(key, "message", "Codex", "running", previous + str(field(payload, "delta", "")),
                     item_type="agentMessage", force=False)
        elif method in {"item/commandExecution/outputDelta", "item/fileChange/outputDelta"}:
            key = str(field(payload, "item_id", ""))
            previous = self.documents.get(key)
            if previous and previous["kind"] == "tool":
                self.put(key, "tool", previous["title"], "running",
                         previous["text"] + str(field(payload, "delta", "")),
                         item_type=previous["item_type"], force=False)
        elif method == "error":
            error = field(payload, "error")
            self.put("sdk-error", "error", "Codex 错误", "failed", str(field(error, "message", "SDK 返回错误")))
        # Unknown/raw-response/reasoning notifications deliberately have no fallback dump.

    def finish(self, status, message):
        for key, document in list(self.documents.items()):
            if document["status"] == "running":
                self.put(key, document["kind"], document["title"], "interrupted", document["text"],
                         item_type=document["item_type"])
        self.put("host-result", "status" if status == "completed" else "error",
                 "提炼完成" if status == "completed" else "提炼停止", status, message)
