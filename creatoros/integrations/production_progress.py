"""Safe live SDK metadata; not a workflow state machine or image counter."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .atomic_file import atomic_write_text

LOG = logging.getLogger(__name__)
OBSERVATION_WARNING = "本次进度或执行记录曾保存失败，部分展示可能不完整；不代表任务执行失败。"
HEALTH_FILE = "observation_health.json"


def read_observation_warning(directory: Path) -> str | None:
    path = directory / HEALTH_FILE
    try:
        if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()) or path.stat().st_size > 4096:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        return OBSERVATION_WARNING if value.get("degraded") is True else None
    except (OSError, ValueError, AttributeError):
        return None


class ProductionProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: Literal["mind", "visual", "production", "research"]
    status: Literal["running", "completed", "failed", "interrupted"]
    started_at: datetime
    last_activity_at: datetime
    last_event: Literal["thread.started", "item.started", "item.completed", "delta", "turn.completed", "stage.failed"]
    activity: Literal["thinking", "reading", "searching", "tool_running", "responding", "waiting", "completed", "failed"]
    completed_tool_calls: int = Field(ge=0)
    total_pages: int | None = Field(default=None, ge=1)
    phase: Literal["planning", "rendering", "assembling"] | None = None
    current_page: int | None = Field(default=None, ge=1)
    completed_pages: int = Field(default=0, ge=0)
    page_attempt: int = Field(default=0, ge=0, le=2)
    observation_warning: str | None = None


TOOLS = {"commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall", "webSearch", "imageGeneration", "collabAgentToolCall"}


class ProgressWriter:
    def __init__(self, directory: Path, stage: str, total_pages: int | None = None):
        self.directory = directory
        self.completed_ids: set[str] = set()
        now = datetime.now(timezone.utc)
        self.state = ProductionProgress(stage=stage, status="running", started_at=now,
                                        last_activity_at=now, last_event="thread.started",
                                        activity="waiting", completed_tool_calls=0, total_pages=total_pages)
        self.last_write = 0.0
        self.diagnostic_failures: set[str] = set()
        self.save()

    def warning(self, component: str, error: OSError):
        self.state.observation_warning = OBSERVATION_WARNING
        if component in self.diagnostic_failures:
            return
        self.diagnostic_failures.add(component)
        # Never log arbitrary exception text, paths or SDK/model payloads.
        LOG.warning("Codex diagnostic %s unavailable (%s); execution continues", component, type(error).__name__)
        try:
            atomic_write_text(self.directory / HEALTH_FILE,
                              json.dumps({"degraded": True, "components": sorted(self.diagnostic_failures)}))
        except OSError:
            LOG.warning("Codex diagnostic health could not be saved; see server log")

    def diagnostic(self, component, action):
        try:
            action()
            return True
        except OSError as error:
            self.warning(component, error)
            return False

    def save(self):
        target = self.directory / "production_progress.json"
        self.diagnostic("progress", lambda: atomic_write_text(target, self.state.model_dump_json()))
        # Throttle even failed writes, rather than retry on every output delta.
        self.last_write = monotonic()

    def _append_trace(self, value):
        with (self.directory / "codex_trace.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False) + "\n")

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
            self.diagnostic("metadata_trace", lambda: self._append_trace({
                "stage": self.state.stage, "type": self.state.last_event, "activity": self.state.activity,
                "at": self.state.last_activity_at.isoformat(), **identity}))
        if self.state.last_event != "delta" or monotonic() - self.last_write >= 0.75:
            self.save()

    def finish(self, status: str):
        self.state.status = status
        self.state.last_event = "turn.completed" if status == "completed" else "stage.failed"
        self.state.activity = "completed" if status == "completed" else "failed"
        self.state.last_activity_at = datetime.now(timezone.utc)
        self.save()

    def page(self, phase: str, order: int | None, completed: int, attempt: int = 0):
        self.state.phase = phase
        self.state.current_page = order
        self.state.completed_pages = completed
        self.state.page_attempt = attempt
        self.state.status = "running"
        self.save()

    def record_usage(self, usage: dict):
        path = self.directory / f"{self.state.stage}_usage.json"
        self.diagnostic("usage", lambda: atomic_write_text(path, json.dumps(usage)))


async def collect_observed_turn(turn, progress: ProgressWriter, public_observer=None, *,
                                public_observer_is_diagnostic=True):
    # SDK 0.157.1 is pinned. Its private collector is the sole compatibility seam;
    # retain final-answer selection, usage and failed-turn semantics instead of duplicating them.
    from openai_codex._run import _collect_async_turn_result
    from .worker_protocol import record_turn
    from .codex_public_events import PublicEventCapture

    phase = getattr(progress, "worker_phase", progress.state.stage)
    capture = PublicEventCapture(progress.directory, turn.id, phase, getattr(turn, "thread_id", ""))
    capture.write("capture/started", {}, status="running")
    record_turn(progress.directory, turn.id, phase, "running")
    stream = turn.stream()
    async def observed():
        async for event in stream:
            capture.observe(event)
            if public_observer is not None:
                if isinstance(progress, ProgressWriter) and public_observer_is_diagnostic:
                    progress.diagnostic("public_activity", lambda: public_observer(event))
                else:
                    # Discussion guards and research task/thread persistence are
                    # critical callbacks, not optional diagnostics.
                    public_observer(event)
            if event.method == "thread/tokenUsage/updated":
                total = getattr(getattr(event.payload, "token_usage", None), "total", None)
                if total is not None:
                    usage = {key: getattr(total, key, 0) for key in (
                        "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")}
                    progress.record_usage(usage)
            # Do not serialize large outputs/reasoning only to throw them away.
            item = getattr(event.payload, "item", None)
            item = getattr(item, "root", item)
            metadata = {"item": {"type": getattr(item, "type", ""), "id": getattr(item, "id", "")}}
            progress.observe(event.method, metadata)
            yield event
    iterator = observed()
    try:
        result = await _collect_async_turn_result(iterator, turn_id=turn.id)
        # The pinned SDK returns interrupted turns normally; only failed raises.
        status = getattr(result.status, "value", result.status)
        capture.finish(status)
        record_turn(progress.directory, turn.id, phase, status)
        return result
    except BaseException as error:
        import asyncio
        capture.finish("interrupted" if isinstance(error, asyncio.CancelledError) else "failed")
        record_turn(progress.directory, turn.id, phase,
                    "interrupted" if isinstance(error, asyncio.CancelledError) else "failed")
        raise
    finally:
        await iterator.aclose()
        await stream.aclose()
