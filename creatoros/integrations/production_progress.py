"""Safe live SDK metadata; not a workflow state machine or image counter."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProductionProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: Literal["mind", "visual", "production"]
    status: Literal["running", "completed", "failed", "interrupted"]
    started_at: datetime
    last_activity_at: datetime
    last_event: Literal["thread.started", "item.started", "item.completed", "delta", "turn.completed", "stage.failed"]
    activity: Literal["thinking", "reading", "searching", "tool_running", "responding", "waiting", "completed", "failed"]
    completed_tool_calls: int = Field(ge=0)
    total_pages: int | None = Field(default=None, ge=1)


TOOLS = {"commandExecution", "mcpToolCall", "dynamicToolCall", "webSearch", "imageGeneration", "collabAgentToolCall"}


class ProgressWriter:
    def __init__(self, directory: Path, stage: str, total_pages: int | None = None):
        self.directory = directory
        self.completed_ids: set[str] = set()
        now = datetime.now(timezone.utc)
        self.state = ProductionProgress(stage=stage, status="running", started_at=now,
                                        last_activity_at=now, last_event="thread.started",
                                        activity="waiting", completed_tool_calls=0, total_pages=total_pages)
        self.last_write = 0.0
        self.save()

    def save(self):
        target = self.directory / "production_progress.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(self.state.model_dump_json(), encoding="utf-8")
        os.replace(temporary, target)
        self.last_write = monotonic()

    def observe(self, method: str, payload: dict):
        # Deliberately discard all model text, command arguments, tool output and errors.
        if method in {"item/started", "item/completed"}:
            item = payload.get("item", {})
            kind = item.get("type", "")
            self.state.last_event = "item.started" if method == "item/started" else "item.completed"
            self.state.activity = ({"reasoning": "thinking", "agentMessage": "responding",
                                    "webSearch": "searching"}.get(kind)
                                   or ("tool_running" if kind in TOOLS else "waiting"))
            if method == "item/completed" and kind in TOOLS and item.get("id"):
                self.completed_ids.add(str(item["id"]))
                self.state.completed_tool_calls = len(self.completed_ids)
        elif method.startswith("item/") and (method.endswith("/delta") or method.endswith("Delta")):
            self.state.last_event = "delta"
            # Tool output deltas mean activity too, not necessarily assistant reasoning.
            self.state.activity = ("responding" if "agentMessage" in method else
                                   "thinking" if "/reasoning/" in method else "tool_running")
        elif method == "turn/completed":
            self.state.last_event = "turn.completed"
            # The SDK collector still checks error/status before finish(completed).
        else:
            return
        self.state.last_activity_at = datetime.now(timezone.utc)
        if self.state.last_event != "delta":
            identity = {}
            if method in {"item/started", "item/completed"}:
                if kind in TOOLS | {"reasoning", "agentMessage"}:
                    identity["item_type"] = kind
                item_id = str(item.get("id", ""))
                if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", item_id):
                    identity["item_id"] = item_id
            with (self.directory / "codex_trace.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"stage": self.state.stage, "type": self.state.last_event,
                                         "activity": self.state.activity,
                                         "at": self.state.last_activity_at.isoformat(), **identity}, ensure_ascii=False) + "\n")
        if self.state.last_event != "delta" or monotonic() - self.last_write >= 0.75:
            self.save()

    def finish(self, status: str):
        self.state.status = status
        self.state.last_event = "turn.completed" if status == "completed" else "stage.failed"
        self.state.activity = "completed" if status == "completed" else "failed"
        self.state.last_activity_at = datetime.now(timezone.utc)
        self.save()


async def collect_observed_turn(turn, progress: ProgressWriter):
    # SDK 0.157.1 is pinned. Its private collector is the sole compatibility seam;
    # retain final-answer selection, usage and failed-turn semantics instead of duplicating them.
    from openai_codex._run import _collect_async_turn_result

    stream = turn.stream()
    async def observed():
        async for event in stream:
            # Do not serialize large outputs/reasoning only to throw them away.
            item = getattr(event.payload, "item", None)
            item = getattr(item, "root", item)
            metadata = {"item": {"type": getattr(item, "type", ""), "id": getattr(item, "id", "")}}
            progress.observe(event.method, metadata)
            yield event
    iterator = observed()
    try:
        return await _collect_async_turn_result(iterator, turn_id=turn.id)
    finally:
        await iterator.aclose()
        await stream.aclose()
