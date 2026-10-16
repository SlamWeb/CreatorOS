"""Isolated SQLite + controlled platform for time/fault/concurrency injection.

No production database or live publication is accessed by this suite.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select

from creatoros.publication.feedback import FeedbackService, INTERVAL
from creatoros.publication.publishing import PublicationService, PublicationBusy
from creatoros.storage import (Base, ContentRepository, CreatorPlatform, ManualPublication,
    PublicationMetric, PublicationRequest, PublicationFeedbackSetting, PublicationFeedbackState, Series)
from creatoros.web.artifacts import StudioArtifacts
from tests.studio_review_fixtures import make_fixture


class ReadPlatform:
    def __init__(self):
        self.account = "account-a"
        self.author = "account-a"
        self.calls = 0
        self.error = False
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def identity(self):
        return {"id": self.account}

    async def inspect(self, value):
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        if self.error:
            raise RuntimeError("secret token must never reach persisted error")
        return {"author_id": self.author, "metrics": {"likes": 3, "favorites": 1, "comments": 0}}

    async def publish(self, *args):
        raise AssertionError("Feedback must NEVER publish")


class FeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="creatoros-feedback-")
        self.db, self.runs, _, self.app = make_fixture(Path(self.temp.name))
        self.clock = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
        self.patches = [patch("creatoros.publication.feedback.now", lambda: self.clock),
                        patch("creatoros.publication.publishing.now", lambda: self.clock)]
        for item in self.patches:
            item.start()
        self.platform = ReadPlatform()
        self.pub = PublicationService(self.db, self.runs, StudioArtifacts(self.db, self.runs.output_root), self.platform)
        self.feedback = FeedbackService(self.db, self.pub)
        self.run = self.runs.create("review-1")
        with self.db.session() as session:
            session.add(PublicationRequest(id="request-a", content_run_id=self.run.id, creator_id="review-lab",
                revision_id="revision-a", artifact_digest="a" * 64, account_id="account-a", account_name="A",
                digest="b" * 64, content_digest="c" * 64, status="published", post_url="https://www.xiaohongshu.com/explore/a",
                payload_json={"title": "测试", "content": "正文", "tags": [], "visibility": "公开可见", "image_hashes": ["x"], "image_count": 1},
                events_json=[{"type": "confirmed"}, {"type": "submitted"}], started_at=self.clock - timedelta(hours=1)))
            session.add(ManualPublication(id="receipt-a", content_run_id=self.run.id, revision_id="revision-a",
                artifact_digest="a" * 64, platform="xiaohongshu", post_url="https://www.xiaohongshu.com/explore/a",
                published_at=self.clock - timedelta(hours=1), recorded_at=self.clock))

    async def asyncTearDown(self):
        self.platform.release.set()
        await self.feedback.shutdown()
        await self.pub.shutdown()
        self.db.close()
        for item in self.patches:
            item.stop()
        self.temp.cleanup()

    async def drain(self):
        jobs = list(self.feedback._jobs.values())
        if jobs:
            await asyncio.gather(*jobs)
        await asyncio.sleep(0)

    def metrics(self):
        with self.db.session() as session:
            return list(session.scalars(select(PublicationMetric)))

    def sample(self, age=0):
        with self.db.session() as session:
            session.add(PublicationMetric(id=f"sample-{age}", publication_id="receipt-a", request_id=f"sample-{age}",
                measured_at=self.clock - timedelta(seconds=age), likes=2, favorites=0, comments=0))

    async def test_migration_and_defaults(self):
        with self.db.engine.connect() as connection:
            self.assertEqual(compare_metadata(MigrationContext.configure(connection), Base.metadata), [])
        view = self.feedback.view("review-lab")
        self.assertFalse(view["enabled"])
        self.assertIsNone(view["summary"]["likes"])
        self.assertEqual(view["summary"]["notes"], 1)
        await self.feedback.tick()
        await self.drain()
        self.assertEqual(self.platform.calls, 0)

    async def test_due_and_restart_persistence(self):
        self.sample(age=1799)
        self.feedback.set_enabled("review-lab", True)
        await self.feedback.tick(); await self.drain()
        self.assertEqual(self.platform.calls, 0)
        self.clock += timedelta(seconds=1)
        await self.feedback.tick(); await self.drain()
        self.assertEqual(self.platform.calls, 1)
        self.assertEqual(len(self.metrics()), 2)
        restarted = FeedbackService(self.db, self.pub)
        self.assertTrue(restarted.view("review-lab")["enabled"])
        await restarted.tick()
        await asyncio.gather(*restarted._jobs.values())
        await restarted.shutdown()
        self.assertEqual(self.platform.calls, 1)

    async def test_manual_refresh_deduplicates_and_auto_honors_snapshot(self):
        self.platform.release.clear()
        first = self.feedback.refresh("review-lab")
        second = self.feedback.refresh("review-lab")
        self.assertTrue(first["checking"] and second["checking"])
        await self.platform.entered.wait()
        self.assertEqual(self.platform.calls, 1)
        self.platform.release.set(); await self.drain()
        self.feedback.set_enabled("review-lab", True)
        await self.feedback.tick(); await self.drain()
        self.assertEqual(self.platform.calls, 1)
        self.assertIsNone(self.feedback.view("review-lab")["summary"]["views"])
        self.assertEqual(self.feedback.view("review-lab")["summary"]["likes"], 3)

    async def test_old_unknown_and_future_notes_never_collected(self):
        self.feedback.set_enabled("review-lab", True)
        for age, status in [(8, "published"), (1, "unknown"), (-1, "published")]:
            with self.db.session() as session:
                session.get(ManualPublication, "receipt-a").published_at = self.clock - timedelta(days=age)
                session.get(PublicationRequest, "request-a").status = status
            await self.feedback.tick(); await self.drain()
            self.assertEqual(self.feedback.view("review-lab")["summary"]["notes"], 0)
        self.assertEqual(self.platform.calls, 0)

    async def test_failure_backoff_and_no_publishing_state_change(self):
        self.sample(age=3600)
        self.platform.error = True
        self.feedback.set_enabled("review-lab", True)
        await self.feedback.tick(); await self.drain()
        view = self.feedback.view("review-lab")
        self.assertNotIn("secret", view["error"])
        self.assertEqual(view["summary"]["likes"], 2)
        self.clock += timedelta(seconds=300)
        await self.feedback.tick(); await self.drain()
        self.assertEqual(self.platform.calls, 1)
        self.clock += timedelta(seconds=1500)
        await self.feedback.tick(); await self.drain()
        with self.db.session() as session:
            state = session.get(PublicationFeedbackState, "request-a")
            self.assertEqual(state.failures, 2)
            self.assertEqual(state.next_attempt_at.replace(tzinfo=timezone.utc), self.clock + timedelta(seconds=3600))
            request = session.get(PublicationRequest, "request-a")
            self.assertEqual(request.status, "published")
            self.assertEqual(len(request.events_json), 2)

    async def test_verified_publication_time_controls_display_and_window(self):
        # A receipt may contain dispatch time, which is not actual publication time.
        actual = self.clock - timedelta(minutes=58)
        with self.db.session() as session:
            session.get(ManualPublication, "receipt-a").published_at = self.clock - timedelta(days=8)
            session.get(PublicationRequest, "request-a").events_json = [
                {"type": "verified", "evidence": {"published_at": actual.astimezone(timezone(timedelta(hours=8))).isoformat()}}]
        self.assertEqual(self.feedback.view("review-lab")["notes"][0]["published_at"], actual.isoformat())
        for remote in [self.clock - timedelta(days=8), self.clock + timedelta(days=1)]:
            with self.db.session() as session:
                session.get(ManualPublication, "receipt-a").published_at = self.clock - timedelta(hours=1)
                session.get(PublicationRequest, "request-a").events_json = [
                    {"type": "verified", "evidence": {"published_at": remote.isoformat()}}]
            self.assertEqual(self.feedback.view("review-lab")["notes"], [])
        for invalid in [None, "malformed", {"bad": "time"}]:
            with self.db.session() as session:
                session.get(PublicationRequest, "request-a").events_json = [
                    {"type": "verified", "evidence": {"published_at": invalid}}]
            self.assertEqual(self.feedback.view("review-lab")["notes"][0]["published_at"],
                (self.clock - timedelta(hours=1)).isoformat())

    async def test_identity_and_note_author_must_match(self):
        self.platform.account = "wrong"
        self.feedback.refresh("review-lab"); await self.drain()
        self.assertEqual(self.platform.calls, 0)
        self.assertEqual(self.metrics(), [])
        self.platform.account = "account-a"
        self.platform.author = "wrong"
        self.feedback.refresh("review-lab"); await self.drain()
        self.assertEqual(self.metrics(), [])
        self.assertIn("登录", self.feedback.view("review-lab")["error"])

    async def test_manual_recovery_clears_failed_auto_backoff(self):
        self.platform.error = True
        self.feedback.refresh("review-lab"); await self.drain()
        self.clock += timedelta(seconds=1)
        self.platform.error = False
        await self.pub.refresh_metrics("request-a")
        view = self.feedback.view("review-lab")
        self.assertIsNone(view["error"])
        self.assertIsNone(view["notes"][0]["error"])
        self.assertEqual(view["notes"][0]["next_refresh_at"], (self.clock + timedelta(seconds=INTERVAL)).isoformat())
        with self.db.session() as session:
            self.assertEqual(session.get(PublicationFeedbackState, "request-a").failures, 0)

    async def test_remote_timeout_releases_browser_and_preserves_data(self):
        self.platform.release.clear()
        with patch("creatoros.publication.feedback.READ_TIMEOUT", 0.05):
            self.feedback.refresh("review-lab"); await self.drain()
        self.assertEqual(self.metrics(), [])
        self.assertIsNotNone(self.feedback.view("review-lab")["error"])
        self.assertFalse(self.pub._mcp_lock.locked())
        async with self.pub.mcp_access():
            pass

    async def test_disabled_during_read_finishes_only_current_read(self):
        self.feedback.set_enabled("review-lab", True)
        self.platform.release.clear()
        await self.feedback.tick()
        await self.platform.entered.wait()
        self.feedback.set_enabled("review-lab", False)
        self.platform.release.set(); await self.drain()
        self.assertEqual(len(self.metrics()), 1)
        self.clock += timedelta(hours=1)
        await self.feedback.tick(); await self.drain()
        self.assertEqual(self.platform.calls, 1)

    async def test_publication_tasks_and_foreground_gate_have_priority(self):
        self.feedback.set_enabled("review-lab", True)
        blocker = asyncio.create_task(asyncio.sleep(100))
        self.pub.tasks.add(blocker)
        await self.feedback.tick(); await self.drain()
        self.assertEqual(self.platform.calls, 0)
        self.pub.tasks.remove(blocker); blocker.cancel()
        await asyncio.gather(blocker, return_exceptions=True)
        async with self.pub.mcp_access():
            await self.feedback.tick(); await self.drain()
            self.assertEqual(self.platform.calls, 0)
        await self.feedback.tick(); await self.drain()
        self.assertEqual(self.platform.calls, 1)

    async def test_parallel_manual_refresh_serialized_with_auto_due_recheck(self):
        self.platform.release.clear()
        manual = asyncio.create_task(self.pub.refresh_metrics("request-a"))
        await self.platform.entered.wait()
        self.feedback.set_enabled("review-lab", True)
        await self.feedback.tick(); await self.drain()
        self.platform.release.set(); await manual
        await self.feedback.tick(); await self.drain()
        self.assertEqual(self.platform.calls, 1)
        self.assertEqual(len(self.metrics()), 1)

    async def test_busy_manual_refresh_reports_noop_and_clears_on_retry(self):
        async with self.pub.mcp_access():
            self.feedback.refresh("review-lab")
            await self.drain()
            self.assertEqual(self.platform.calls, 0)
            self.assertEqual(self.feedback.view("review-lab")["error"],
                "发布或其他平台操作正在进行，请稍后刷新。")
        self.feedback.refresh("review-lab")
        await self.drain()
        self.assertEqual(self.platform.calls, 1)
        self.assertIsNone(self.feedback.view("review-lab")["error"])

    async def test_shutdown_keeps_retry_window_without_false_data(self):
        self.platform.release.clear()
        self.feedback.refresh("review-lab")
        await self.platform.entered.wait()
        await self.feedback.shutdown()
        self.assertEqual(self.metrics(), [])
        with self.db.session() as session:
            self.assertIsNotNone(session.get(PublicationFeedbackState, "request-a").next_attempt_at)
            self.assertEqual(session.get(PublicationRequest, "request-a").status, "published")

    async def test_account_isolation_after_series_reassignment(self):
        content = ContentRepository(self.db)
        content.create_creator(creator_id="other", display_name="Other", platform=CreatorPlatform.XIAOHONGSHU)
        with self.db.session() as session:
            series = session.get(Series, "agent-notes")
            series.creator_id, series.name = "other", "Other private column"
        old = self.feedback.view("review-lab")
        other = self.feedback.view("other")
        self.assertIsNone(old["notes"][0]["series_id"])
        self.assertNotIn("private", old["notes"][0]["series_name"])
        self.assertEqual(other["notes"], [])

    async def test_gate_is_reentrant_and_background_does_not_queue(self):
        async with self.pub.mcp_access():
            async with self.pub.mcp_access():
                pass
            async def other_task():
                with self.assertRaises(PublicationBusy):
                    async with self.pub.mcp_access(background=True):
                        self.fail("Should yield")
            await asyncio.create_task(other_task())


if __name__ == "__main__":
    unittest.main()
