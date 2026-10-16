"""Opt-in local feedback collection. No LLM, publishing, or context mutation."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging

from sqlalchemy import select

from creatoros.storage import (ContentRun, Creator, ManualPublication,
    PublicationFeedbackSetting, PublicationFeedbackState, PublicationMetric,
    PublicationRequest, Series, Topic)
from .publishing import PublicationBusy, fail, now, utc

METRICS = ("likes", "favorites", "comments", "views", "shares")
HEARTBEAT = 300
INTERVAL = 1800
WINDOW_DAYS = 7
READ_TIMEOUT = 120
BUSY_MESSAGE = "发布或其他平台操作正在进行，请稍后刷新。"
log = logging.getLogger(__name__)


def iso(value):
    return utc(value).isoformat() if value else None


def published_time(request, receipt):
    """Prefer the verified remote timestamp; old receipts used dispatch time."""
    for item in reversed(request.events_json or []):
        if not isinstance(item, dict) or item.get("type") != "verified":
            continue
        evidence = item.get("evidence")
        value = evidence.get("published_at") if isinstance(evidence, dict) else None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
            return utc(parsed)
        except (AttributeError, TypeError, ValueError, OverflowError):
            break
    return utc(receipt.published_at)


class FeedbackService:
    def __init__(self, database, publishing):
        self.database, self.publishing = database, publishing
        self._loop_task = None
        self._jobs = {}
        self._next_check = None
        self._stopping = False

    def start(self):
        if self._loop_task is None or self._loop_task.done():
            self._stopping = False
            self._next_check = now()
            self._loop_task = asyncio.create_task(self._loop())

    async def shutdown(self):
        self._stopping = True
        tasks = [task for task in [self._loop_task, *self._jobs.values()] if task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._jobs.clear()
        self._next_check = None

    async def _loop(self):
        while not self._stopping:
            self._next_check = now() + timedelta(seconds=HEARTBEAT)
            try:
                await self.tick()
            except Exception:
                # Keep the heartbeat alive; never log raw transport credentials.
                log.warning("Feedback heartbeat failed; retrying on next heartbeat.")
            await asyncio.sleep(HEARTBEAT)

    def _creator(self, session, creator_id):
        creator = session.get(Creator, creator_id)
        if not creator or not creator.is_active:
            fail("账号不存在。", "not_found", 404)

    def set_enabled(self, creator_id, enabled):
        with self.database.session() as session:
            self._creator(session, creator_id)
            setting = session.get(PublicationFeedbackSetting, creator_id)
            if not setting:
                setting = PublicationFeedbackSetting(creator_id=creator_id)
                session.add(setting)
            setting.enabled = enabled
        # No new remote request is started here. An in-flight read may finish.
        return self.view(creator_id)

    def _start_job(self, creator_id, *, automatic):
        existing = self._jobs.get(creator_id)
        if self._stopping or (existing and not existing.done()):
            return
        task = asyncio.create_task(self._collect(creator_id, automatic=automatic))
        self._jobs[creator_id] = task
        def finished(done):
            if self._jobs.get(creator_id) is done:
                self._jobs.pop(creator_id, None)
            if not done.cancelled() and done.exception():
                log.warning("Feedback collection stopped; saved data retained.")
        task.add_done_callback(finished)

    def refresh(self, creator_id):
        with self.database.session() as session:
            self._creator(session, creator_id)
        self._start_job(creator_id, automatic=False)
        return self.view(creator_id)

    async def tick(self):
        with self.database.session() as session:
            enabled = list(session.scalars(select(PublicationFeedbackSetting.creator_id).join(
                Creator, Creator.id == PublicationFeedbackSetting.creator_id).where(
                PublicationFeedbackSetting.enabled.is_(True), Creator.is_active.is_(True))))
        for creator_id in enabled:
            self._start_job(creator_id, automatic=True)

    def _notes(self, session, creator_id):
        stamp = now()
        pairs = session.execute(select(PublicationRequest, ManualPublication).join(
            ManualPublication, ManualPublication.content_run_id == PublicationRequest.content_run_id).where(
            PublicationRequest.creator_id == creator_id, PublicationRequest.status == "published")).all()
        notes = []
        for request, receipt in pairs:
            published_at = published_time(request, receipt)
            if not stamp - timedelta(days=WINDOW_DAYS) <= published_at <= stamp:
                continue
            state = session.get(PublicationFeedbackState, request.id)
            samples = [{"measured_at": iso(item.measured_at), **{key: getattr(item, key) for key in METRICS}}
                for item in session.scalars(select(PublicationMetric).where(
                    PublicationMetric.publication_id == receipt.id).order_by(PublicationMetric.measured_at, PublicationMetric.id))]
            latest = datetime.fromisoformat(samples[-1]["measured_at"]) if samples else None
            due = latest + timedelta(seconds=INTERVAL) if latest else stamp
            if state and state.next_attempt_at:
                due = max(due, utc(state.next_attempt_at))
            run = session.get(ContentRun, request.content_run_id)
            topic = session.get(Topic, run.topic_id) if run else None
            series = session.get(Series, topic.series_id) if topic else None
            same_owner = series and series.creator_id == creator_id
            notes.append({"request_id": request.id, "run_id": request.content_run_id,
                "series_id": series.id if same_owner else None,
                "series_name": series.name if same_owner else "原栏目",
                "title": request.payload_json.get("title", ""), "post_url": request.post_url,
                "published_at": iso(published_at), "last_updated_at": iso(latest),
                "next_refresh_at": iso(due), "error": state.last_error if state else None, "samples": samples})
        return sorted(notes, key=lambda note: note["published_at"], reverse=True)

    def view(self, creator_id):
        with self.database.session() as session:
            self._creator(session, creator_id)
            setting = session.get(PublicationFeedbackSetting, creator_id)
            notes = self._notes(session, creator_id)
            summary = {"notes": len(notes), "sampled_notes": sum(bool(note["samples"]) for note in notes)}
            for key in METRICS:
                values = [note["samples"][-1][key] if note["samples"] else None for note in notes]
                summary[key] = sum(values) if values and all(value is not None for value in values) else None
            job = self._jobs.get(creator_id)
            enabled = bool(setting and setting.enabled)
            return {"creator_id": creator_id, "enabled": enabled,
                "checking": bool(job and not job.done()), "checked_at": iso(setting.checked_at) if setting else None,
                "last_updated_at": max((note["last_updated_at"] for note in notes if note["last_updated_at"]), default=None),
                "next_check_at": iso(self._next_check) if enabled else None,
                "heartbeat_seconds": HEARTBEAT, "refresh_seconds": INTERVAL, "window_days": WINDOW_DAYS,
                "error": (BUSY_MESSAGE if setting and setting.last_error == BUSY_MESSAGE else
                    next((note["error"] for note in notes if note["error"]), None)),
                "summary": summary, "notes": notes}

    async def _refresh_note(self, note, automatic):
        async with self.publishing.mcp_access(background=True):
            with self.database.session() as session:
                state = session.get(PublicationFeedbackState, note["request_id"])
                if not state:
                    state = PublicationFeedbackState(request_id=note["request_id"], failures=0)
                    session.add(state)
                state.attempted_at = now()
                state.next_attempt_at = now() + timedelta(seconds=INTERVAL)
            return await self.publishing.refresh_metrics(note["request_id"],
                min_interval_seconds=INTERVAL if automatic else 0)

    async def _collect(self, creator_id, *, automatic):
        with self.database.session() as session:
            self._creator(session, creator_id)
            setting = session.get(PublicationFeedbackSetting, creator_id)
            if not setting:
                setting = PublicationFeedbackSetting(creator_id=creator_id, enabled=False)
                session.add(setting)
            if automatic and not setting.enabled:
                return
            setting.checked_at = now()
            setting.last_error = None
            notes = self._notes(session, creator_id)
        for note in notes:
            with self.database.session() as session:
                setting = session.get(PublicationFeedbackSetting, creator_id)
                creator = session.get(Creator, creator_id)
                if not creator or not creator.is_active or (automatic and not setting.enabled):
                    return
            if automatic and datetime.fromisoformat(note["next_refresh_at"]) > now():
                continue
            # Gate before persisting an attempt: yielding is not a failed request.
            try:
                result = await asyncio.wait_for(self._refresh_note(note, automatic), timeout=READ_TIMEOUT)
                latest = datetime.fromisoformat(result["metrics"][-1]["measured_at"])
                with self.database.session() as session:
                    state = session.get(PublicationFeedbackState, note["request_id"])
                    state.failures, state.last_error = 0, None
                    state.next_attempt_at = latest + timedelta(seconds=INTERVAL)
            except PublicationBusy:
                # Foreground publication/manual checks always have priority.
                if not automatic:
                    with self.database.session() as session:
                        session.get(PublicationFeedbackSetting, creator_id).last_error = BUSY_MESSAGE
                return
            except asyncio.CancelledError:
                # Keep the persisted retry window, never touch publication status.
                raise
            except Exception as exc:
                message = "账号登录不匹配，请确认小红书登录账号。" if getattr(exc, "code", None) == "account_mismatch" else "数据更新失败，已保留上次数据；稍后重试或检查小红书连接。"
                with self.database.session() as session:
                    state = session.get(PublicationFeedbackState, note["request_id"])
                    state.failures += 1
                    state.last_error = message
                    state.next_attempt_at = now() + timedelta(seconds=min(21600, INTERVAL * (2 ** min(state.failures - 1, 4))))
                    session.get(PublicationFeedbackSetting, creator_id).last_error = message
