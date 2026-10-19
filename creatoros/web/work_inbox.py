"""A bounded, read-only index of existing work, not a second task state machine."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Query, Response
from sqlalchemy import func, select

from creatoros.storage import ContentAttempt, ContentRevision, ContentRun, Creator, Database, Series, SkillCalibration, Topic, TopicRemoval


_ACTIVE = {"queued", "researching", "running", "producing", "validating"}
_ATTENTION = {"ready", "awaiting_approval", "failed", "interrupted", "stale"}


def _iso(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.replace(tzinfo=value.tzinfo or timezone.utc).isoformat()


def _status(value):
    return value.value if hasattr(value, "value") else str(value)


def _metadata(root: Path, pattern: str):
    """Read metadata only; never resolve catalog working copies or trial artifacts."""
    result, skipped = [], 0
    if not root.exists():
        return result, skipped
    for path in root.glob(pattern):
        try:
            if (path.is_symlink() or not path.resolve().is_relative_to(root.resolve())
                    or path.stat().st_size > 2 * 1024 * 1024):
                skipped += 1
                continue
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or not isinstance(value.get("id"), str):
                raise ValueError("invalid work metadata")
            if any(value.get(key) is not None and not isinstance(value[key], dict) for key in ("snapshot", "progress", "actions")):
                raise ValueError("invalid work metadata")
            if any(not isinstance(value.get(key, []), list) or any(not isinstance(item, dict) for item in value.get(key, []))
                   for key in ("skills", "saved_skills", "trials")):
                raise ValueError("invalid work metadata")
            result.append(value)
        except (OSError, ValueError):
            skipped += 1
    return result, skipped


def _item(kind, identifier, title, status, created_at, updated_at, url, *, series_id=None,
          skill_ids=None, version=""):
    return {"key": f"{kind}:{identifier}", "token": f"{kind}:{identifier}:{status}:{version}",
            "kind": kind, "id": identifier, "title": str(title or "未命名任务")[:160],
            "status": status, "bucket": "active" if status in _ACTIVE else "attention" if status in _ATTENTION else "recent",
            "created_at": _iso(created_at), "updated_at": _iso(updated_at or created_at),
            "url": url, "series_id": series_id, "skill_ids": skill_ids or []}


def work_inbox_routes(db: Database, research, extractions) -> APIRouter:
    router = APIRouter(prefix="/api/work-inbox")

    @router.get("")
    def inbox(response: Response, limit: int = Query(50, ge=1, le=100)):
        response.headers["Cache-Control"] = "no-store"
        # SELECT only. In particular, do not construct services or normalize stored tasks.
        with db.session() as session:
            series_rows = list(session.execute(select(Series, Creator).outerjoin(Creator, Series.creator_id == Creator.id)
                                              .where(Series.is_active.is_(True))))
            visible_series = {row.id: row for row, creator in series_rows
                              if row.creator_id is None or (creator is not None and creator.is_active)}
            run_rows = list(session.execute(select(ContentRun, Topic, ContentRevision)
                .join(Topic, ContentRun.topic_id == Topic.id)
                .outerjoin(ContentRevision, (ContentRevision.content_run_id == ContentRun.id)
                           & (ContentRevision.revision_number == ContentRun.active_revision_number))
                .where(Topic.id.not_in(select(TopicRemoval.topic_id)))
                .order_by(ContentRun.updated_at.desc()).limit(500)))
            calibrations = {row.job_id: row for row in session.scalars(select(SkillCalibration))}
            attempt_numbers = dict(session.execute(select(ContentAttempt.revision_id, func.max(ContentAttempt.attempt_number))
                .where(ContentAttempt.revision_id.in_([revision.id for _, _, revision in run_rows if revision]))
                .group_by(ContentAttempt.revision_id)).all())

        items, objects = [], []
        for series in visible_series.values():
            created = _item("series", series.id, series.name, "created", series.created_at, series.created_at,
                            f"/?series={quote(series.id, safe='')}", series_id=series.id)
            items.append(created)
            objects.append({"kind": "series", "id": series.id, "token": created["token"],
                            "created_at": _iso(series.created_at)})
        for run, topic, revision in run_rows:
            series = visible_series.get(topic.series_id)
            if series is None or (run.input_snapshot_json or {}).get("creator_id") != series.creator_id:
                continue
            items.append(_item("production", run.id, topic.title, _status(run.status), run.created_at,
                               run.updated_at, f"/runs/{quote(run.id, safe='')}", series_id=series.id,
                               version=f"r{run.active_revision_number}:a{attempt_numbers.get(revision.id, 0) if revision else 0}:{revision.artifact_digest if revision else ''}"))

        batches, skipped_research = _metadata(Path(research.root), "batches/*.json")
        for batch in batches:
            series = visible_series.get(batch.get("series_id"))
            if series is None:
                continue
            snapshot = batch.get("snapshot") or {}
            creator_snapshot = snapshot.get("creator")
            frozen_owner = creator_snapshot.get("id") if isinstance(creator_snapshot, dict) else snapshot.get("creator_id")
            if frozen_owner != series.creator_id:
                continue
            items.append(_item("research", batch["id"], batch.get("instructions") or f"{series.name} 选题调研",
                str(batch.get("status", "unknown")), batch.get("created_at"),
                (batch.get("progress") or {}).get("last_activity_at") or batch.get("created_at"),
                f"/series/{quote(series.id, safe='')}?research={quote(batch['id'], safe='')}", series_id=series.id,
                version=str(batch.get("attempt", 0))))

        jobs, skipped_jobs = _metadata(Path(extractions.root), "jobs/*/job.json")
        for job in jobs:
            saved_ids = [skill["id"] for skill in job.get("saved_skills", [])
                         if isinstance(skill, dict) and isinstance(skill.get("id"), str)]
            calibration = calibrations.get(job["id"])
            if job.get("task_kind") == "calibrate" and (calibration is None or calibration.series_id not in visible_series
                    or visible_series[calibration.series_id].creator_id != calibration.creator_id):
                continue
            kind = "calibration" if calibration is not None else "extraction"
            label = "Skill 校准" if kind == "calibration" else "Skill 融合" if job.get("task_kind") == "merge" else "Skill 提炼"
            skill_names = "、".join(str(s.get("display_name") or s.get("name") or "") for s in job.get("skills", []) if isinstance(s, dict))
            title = job.get("calibration_target_name") or skill_names or str(job.get("instruction") or job.get("source_text") or label)
            status = "applied" if calibration is not None and calibration.status == "applied" else str(job.get("status", "unknown"))
            trials = job.get("trials", [])
            if status != "applied":
                active_trial = next((trial for trial in reversed(trials) if trial.get("status") == "running"), None)
                if active_trial or job.get("operation"):
                    status = "running"
                elif job.get("error") and status == "ready":
                    status = "failed"  # The saved draft remains; the latest revise failed.
                elif trials and trials[-1].get("digest") == job.get("digest") and trials[-1].get("status") in {"failed", "interrupted"}:
                    status = trials[-1]["status"]
            trial_version = ":".join(f"{t.get('id', '')}-{t.get('status', '')}" for t in job.get("trials", []))
            item = _item(kind, job["id"], title, status, job.get("created_at"), job.get("updated_at"),
                         (f"/skills?extraction={quote(job['id'], safe='')}&calibration={quote(calibration.id, safe='')}"
                          if calibration else f"/skills?extraction={quote(job['id'], safe='')}"),
                         skill_ids=[calibration.skill_id] if calibration else saved_ids,
                         series_id=calibration.series_id if calibration else None,
                         version=f"{job.get('revision', 1)}:{job.get('digest', '')}:a{len(job.get('actions') or {})}:{trial_version}")
            item["label"] = label
            items.append(item)
            for skill_id in saved_ids:
                objects.append({"kind": "skill", "id": skill_id, "token": item["token"],
                                "created_at": _iso(job.get("created_at"))})

        # A Skill may have been registered by more than one job; retain its latest receipt.
        versions = {}
        for obj in objects:
            key = (obj["kind"], obj["id"])
            if key not in versions or obj["created_at"] >= versions[key]["created_at"]:
                versions[key] = obj
        objects = list(versions.values())
        priority = {"active": 0, "attention": 1, "recent": 2}
        items.sort(key=lambda item: item["updated_at"], reverse=True)
        items.sort(key=lambda item: priority[item["bucket"]])
        active = [item for item in items if item["bucket"] == "active"][:limit]
        attention = [item for item in items if item["bucket"] == "attention"]
        recent = [item for item in items if item["bucket"] == "recent"]
        # Old pending approvals must not hide a newly saved Skill forever.
        slots = limit - len(active)
        reserve = min(len(recent), max(1, limit // 5), slots)
        selected_attention = attention[:max(0, slots - reserve)]
        selected_recent = recent[:slots - len(selected_attention)]
        if len(selected_attention) + len(selected_recent) < slots:
            selected_attention = attention[:slots - len(selected_recent)]
        selected = active + selected_attention + selected_recent
        return {"items": selected, "objects": objects,
                "counts": {bucket: sum(item["bucket"] == bucket for item in items) for bucket in priority},
                "has_more": len(items) > limit, "unreadable_records": skipped_research + skipped_jobs,
                "as_of": datetime.now(timezone.utc).isoformat()}

    return router
