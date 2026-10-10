"""Read-only task summaries projected from existing CreatorOS records."""
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from creatoros.storage import ContentRun, ContentRunStatus, Creator, Database, Series, Topic, TopicRemoval


_ACTIVE = {"researching", "running", "producing", "validating"}
_FAILED = {"failed", "interrupted"}


def _iso(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _status(value):
    return value.value if hasattr(value, "value") else str(value)


def worker_task_routes(db: Database, research, discussions) -> APIRouter:
    router = APIRouter()

    @router.get("/api/creators/{creator_id}/tasks")
    def list_creator_tasks(creator_id: str, series_id: str | None = Query(default=None, min_length=1),
                          statuses: list[Literal["queued", "researching", "running", "producing", "validating",
                                                 "ready", "completed", "awaiting_approval", "approved",
                                                 "interrupted", "failed", "cancelled", "unknown", "stale"]]
                          | None = Query(default=None, min_length=1, max_length=14)):
        with db.session() as session:
            creator = session.get(Creator, creator_id)
            if creator is None or not creator.is_active:
                raise HTTPException(404, "账号不存在或已停用。")
            series_query = select(Series).where(Series.creator_id == creator_id)
            if series_id is not None:
                series_query = series_query.where(Series.id == series_id)
            series_rows = list(session.scalars(series_query.order_by(Series.id)))
            if series_id is not None and not series_rows:
                raise HTTPException(404, "栏目不存在或不属于当前账号。")
            series_ids = [row.id for row in series_rows]
            frozen_runs = list(session.scalars(select(ContentRun).join(
                Topic, ContentRun.topic_id == Topic.id).where(Topic.series_id.in_(series_ids)))) if series_ids else []
            stale_series = {session.get(Topic, run.topic_id).series_id for run in frozen_runs
                            if (run.input_snapshot_json or {}).get("creator_id") != creator_id}
            if series_id is not None and series_id in stale_series:
                raise HTTPException(403, "栏目含有其他账号的冻结任务，当前账号不能读取其任务列表。")
            series_rows = [row for row in series_rows if row.id not in stale_series]

            run_query = (select(ContentRun, Topic, Series)
                         .join(Topic, ContentRun.topic_id == Topic.id)
                         .join(Series, Topic.series_id == Series.id)
                         .where(Series.creator_id == creator_id))
            if series_id is not None:
                run_query = run_query.where(Series.id == series_id)
            run_rows = list(session.execute(run_query))
            removed = set(session.scalars(select(TopicRemoval.topic_id)))

            # A Series transfer must not expose a Run frozen for its former owner.
        runs = [(run, topic, series) for run, topic, series in run_rows
                if (run.input_snapshot_json or {}).get("creator_id") == creator_id]

        items = []
        for run, topic, series in runs:
            if topic.id in removed:
                continue
            status = _status(run.status)
            items.append({
                "id": run.id, "kind": "production", "title": topic.title,
                "status": status, "series_id": series.id, "run_id": run.id,
                "url": f"/runs/{run.id}", "updated_at": _iso(run.updated_at),
                "last_activity_at": None,
            })

        for series in series_rows:
            for batch in research.list(series.id):
                try:
                    detail = research.get(batch["id"])
                except (KeyError, OSError, ValueError):
                    detail = batch
                status = _status(batch.get("status", "unknown"))
                created = batch.get("created_at")
                progress = detail.get("progress") if isinstance(detail, dict) else None
                last_activity = progress.get("last_activity_at") if isinstance(progress, dict) else None
                items.append({
                    "id": batch["id"], "kind": "research",
                    "title": (detail.get("instructions") if isinstance(detail, dict) else None)
                             or batch.get("note") or f"{series.name} 选题调研",
                    "status": status, "series_id": series.id, "run_id": None,
                    "url": f"/series/{series.id}?research={batch['id']}",
                    "updated_at": _iso(last_activity or created),
                    "last_activity_at": _iso(last_activity),
                })

        for record in discussions.list_for_creator(creator_id, series_id=series_id):
            # Recheck current and frozen ownership before projecting async records.
            if record.get("creator_id") != creator_id:
                continue
            if series_id is not None and record.get("series_id") != series_id:
                continue
            run_id = record.get("run_id")
            valid = next(((run, topic, series) for run, topic, series in runs if run.id == run_id), None)
            if valid is None:
                continue
            run, topic, series = valid
            status = _status(record.get("status", "unknown"))
            message = record.get("message") or ""
            items.append({
                "id": record.get("id") or record.get("request_id"),
                "kind": "discussion", "title": f"讨论：{message[:100]}" or f"讨论：{topic.title}",
                "status": status, "series_id": series.id, "run_id": run.id,
                "url": f"/runs/{run.id}?discussion={record.get('id', '')}&revision={record.get('revision_id', '')}",
                "updated_at": _iso(record.get("updated_at") or record.get("created_at")),
                "last_activity_at": _iso(record.get("last_activity_at")),
            })

        if statuses is not None:
            items = [item for item in items if item["status"] in statuses]
        items.sort(key=lambda item: item["updated_at"] or "", reverse=True)
        item_statuses = [item["status"] for item in items]
        return {
            "items": items,
            "filter": {"series_id": series_id, "statuses": statuses},
            "summary": {
                # A production queued revision awaits an explicit Execute action;
                # it is not a live worker and must not trigger endless UI polling.
                "active": sum(item["status"] in _ACTIVE or
                              (item["kind"] != "production" and item["status"] == "queued")
                              for item in items),
                "awaiting_approval": sum(status == ContentRunStatus.AWAITING_APPROVAL.value
                                          for status in item_statuses),
                "failed": sum(status in _FAILED for status in item_statuses),
            },
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    return router
