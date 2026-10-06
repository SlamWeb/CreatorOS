"""Durable SDK receipts. Business status stays in the existing Run/batch store."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def _write(path: Path, value: dict):
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def record_task(directory: Path, *, kind: str, scope: dict, input_ref: str, deliverable: str):
    _write(directory / "worker_task.json", {
        "protocol": "creatoros-worker-v1", "kind": kind, "scope": scope,
        "input_ref": input_ref, "deliverable": deliverable,
    })


def record_thread(directory: Path, thread_id: str):
    _write(directory / "worker_receipt.json", {
        "protocol": "creatoros-worker-v1", "thread_id": thread_id, "turns": [],
    })


def record_turn(directory: Path, turn_id: str, phase: str, status: str):
    path = directory / "worker_receipt.json"
    if not path.is_file():
        return  # Legacy producers and extraction retain their existing contracts.
    value = json.loads(path.read_text(encoding="utf-8"))
    turn = next((t for t in value["turns"] if t["id"] == turn_id), None)
    if turn is None:
        turn = {"id": turn_id, "phase": phase}
        value["turns"].append(turn)
    turn.update(status=status, updated_at=datetime.now(timezone.utc).isoformat())
    _write(path, value)


def completed_delivery_turn(directory: Path, thread_id: str) -> bool:
    """A success in planning (or an earlier turn) cannot finalize production."""
    path = directory / "worker_receipt.json"
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 256_000:
        return False
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        turns = value.get("turns", [])
        return (value.get("protocol") == "creatoros-worker-v1"
                and value.get("thread_id") == thread_id and bool(turns)
                and turns[-1].get("phase") in {"production", "delivery_repair"}
                and turns[-1].get("status") == "completed")
    except (ValueError, TypeError, AttributeError):
        return False
