"""Synthetic media lifecycle fixtures for expansion suite 3.

All status files and import receipts are written under the disposable
BenchmarkWorld material root. This module never contacts a media provider.
"""
from __future__ import annotations

import hashlib
import json


SCENARIO = "expansion_extra3_media_states"
IDS = {"Z01", "Z02", "Z03", "Z04"}


def _status_path(world, material_id):
    audio = world.app.state.materials.resolve("bench-a", material_id)
    return audio["path"].parent / "bgm-analysis-status.json"


def _analysis(world, material_id):
    store = world.app.state.materials
    audio = store.resolve("bench-a", material_id)
    duration = float(audio["duration_seconds"])
    store.save_analysis(
        "bench-a",
        material_id,
        {"summary": "隔离夹具中的旧分析摘要", "bpm": 120},
        {"inputSha256": audio["sha256"], "sourceStartSeconds": 0,
         "sourceEndSeconds": duration},
        {"analysisOffset": 0, "analysisDurationSeconds": duration,
         "events": [{"id": "fixture-onset-1", "timeSeconds": duration / 2,
                     "accepted": True}]},
    )


def _write_import(world, *, source_text, row):
    owner = world.variables["creator_a"]
    identifier = hashlib.sha256((owner + ":" + source_text).encode("utf-8")).hexdigest()
    path = world.app.state.materials._import_path(owner, identifier)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"id": identifier, **row}, ensure_ascii=False), encoding="utf-8")
    return identifier


def seed(world, case):
    """Install a frozen, account-local media state before the before snapshot."""
    if world is None or case.get("scenario") != SCENARIO or case.get("id") not in IDS:
        return None

    material_id = world.variables["bgm_material"]
    if case["id"] == "Z01":
        # A failed forced reanalysis does not remove an older usable result.
        _analysis(world, material_id)
        _status_path(world, material_id).write_text(
            json.dumps({"status": "failed", "message": "隔离测试：重分析失败"}, ensure_ascii=False),
            encoding="utf-8",
        )
    elif case["id"] == "Z02":
        # MaterialStore interprets a persisted running marker after restart as
        # interrupted; no analysis bundle exists for this material.
        _status_path(world, material_id).write_text('{"status":"running"}', encoding="utf-8")
    elif case["id"] == "Z03":
        source = "https://www.bilibili.com/video/BV1CREATOROSFIXTURE"
        world.variables["failed_import"] = _write_import(
            world, source_text=source,
            row={"status": "failed", "source_url": source,
                  "message": "隔离测试：来源暂不可用"},
        )
    elif case["id"] == "Z04":
        source = "https://www.bilibili.com/video/BV1CREATOROSREADY"
        world.variables["completed_import"] = _write_import(
            world, source_text=source,
            row={"status": "completed", "source_url": source,
                  "material_id": material_id, "analysis_status": "running"},
        )
        # Import finished, but its separately tracked analysis worker did not.
        _status_path(world, material_id).write_text('{"status":"running"}', encoding="utf-8")
    return None
