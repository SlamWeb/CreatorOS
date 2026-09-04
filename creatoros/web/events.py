from __future__ import annotations

import asyncio
import json
from datetime import timezone
from functools import partial
from time import monotonic

from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from creatoros.runs import ContentRunError
from creatoros.storage import ContentRun, ContentRunEvent
from .schemas import EventBatch, RunEventView


class StudioEvents:
    def __init__(self, database):
        self.database = database

    def snapshot(self, run_id: str) -> dict:
        with self.database.session() as session:
            # One statement so the state and watermark refer to the same DB snapshot.
            row = session.execute(select(ContentRun.status, ContentRun.version, func.max(ContentRunEvent.id))
                .outerjoin(ContentRunEvent, ContentRunEvent.content_run_id == ContentRun.id)
                .where(ContentRun.id == run_id).group_by(ContentRun.id)).first()
            if row is None:
                raise ContentRunError("运行不存在。", status_code=404, code="not_found")
            return {"run_id": run_id, "status": row[0].value, "version": row[1], "latest_event_id": row[2] or 0}

    def batch(self, run_id: str, after_id: int = 0, limit: int = 100) -> EventBatch:
        with self.database.session() as session:
            rows = session.scalars(select(ContentRunEvent).where(
                ContentRunEvent.content_run_id == run_id, ContentRunEvent.id > after_id)
                .order_by(ContentRunEvent.id).limit(limit))
            items = [RunEventView(id=row.id, run_id=row.content_run_id, revision_id=row.revision_id,
                attempt_id=row.attempt_id, event_type=row.event_type.value, from_status=row.from_status,
                to_status=row.to_status, created_at=row.created_at.replace(tzinfo=timezone.utc) if row.created_at.tzinfo is None else row.created_at)
                for row in rows]
            return EventBatch(items=items, next_after_id=items[-1].id if items else after_id)

    async def stream(self, request, run_id: str, after_id: int, snapshot: dict):
        # No id here: advancing it to latest_event_id would skip reconnect replay.
        yield _frame("snapshot", snapshot)
        last_keepalive = monotonic()
        while not getattr(request.app.state, "stop_observers", False) and not await request.is_disconnected():
            batch = await run_in_threadpool(partial(self.batch, run_id, after_id))
            for item in batch.items:
                yield _frame("run_event", item.model_dump(mode="json"), item.id)
                after_id = item.id
            if monotonic() - last_keepalive >= 15:
                yield ": keepalive\n\n"
                last_keepalive = monotonic()
            if len(batch.items) < 100:
                await asyncio.sleep(1)


def _frame(event: str, data: dict, event_id: int | None = None) -> str:
    prefix = f"id: {event_id}\n" if event_id is not None else "retry: 2000\n"
    return f"{prefix}event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
