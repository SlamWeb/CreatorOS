"""Real HTTP and isolated SQLite/files; no model or worker is invoked."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from fastapi import FastAPI
import httpx

from creatoros.storage import (Base, ContentAttempt, ContentAttemptStatus, ContentRevision, ContentRun, ContentRunStatus, Creator,
                              CreatorPlatform, Database, Series, SkillCalibration, Topic, TopicSource)
from creatoros.web.work_inbox import work_inbox_routes
from tests.agent_studio_support import serve


class WorkInboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = Database(f"sqlite:///{(self.root / 'test.db').as_posix()}")
        Base.metadata.create_all(self.db.engine)
        self.research = SimpleNamespace(root=self.root / "research")
        self.extractions = SimpleNamespace(root=self.root / "extractions")
        self.app = FastAPI()
        self.app.include_router(work_inbox_routes(self.db, self.research, self.extractions))
        with self.db.session() as session:
            session.add(Creator(id="account", display_name="测试账号", platform=CreatorPlatform.XIAOHONGSHU))
        with self.db.session() as session:
            session.add(Series(id="series", creator_id="account", name="测试栏目", description="", audience="", skill_name="skill"))
        with self.db.session() as session:
            session.add(Topic(id="topic", series_id="series", title="完成的作品", source=TopicSource.MANUAL, position=1))
        with self.db.session() as session:
            session.add(ContentRun(id="run", topic_id="topic", idempotency_key="content:topic",
                status=ContentRunStatus.AWAITING_APPROVAL, input_snapshot_json={"creator_id": "account"}))
        with self.db.session() as session:
            session.add(ContentRevision(id="revision", content_run_id="run", revision_number=1,
                production_input_json={}, artifact_digest="a" * 64))

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def write(self, path, document):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    def hashes(self):
        return {str(path.relative_to(self.root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.root.rglob("*") if path.is_file()}

    def get(self, query=""):
        with serve(self.app) as base, httpx.Client(base_url=base) as client:
            return client.get("/api/work-inbox" + query)

    def test_real_http_projects_read_only_without_scanning_skill_files(self):
        self.write(self.research.root / "batches/research.json", {"id": "research", "series_id": "series",
            "status": "failed", "created_at": "2026-10-20T01:00:00Z", "instructions": "找故事",
            "snapshot": {"creator": {"id": "account"}}, "note": "failure"})
        self.write(self.extractions.root / "jobs/extract/job.json", {"id": "extract", "status": "saved",
            "created_at": "2026-10-20T02:00:00Z", "updated_at": "2026-10-20T02:01:00Z", "revision": 1,
            "digest": "b" * 64, "skills": [{"name": "故事"}], "saved_skills": [{"id": "new-skill"}]})
        # Browsing the index must not touch even malformed draft/Skill content.
        (self.extractions.root / "jobs/extract/draft").mkdir()
        (self.extractions.root / "jobs/extract/draft/SKILL.md").write_bytes(b"not a skill")
        before = self.hashes()
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        result = response.json()
        self.assertEqual({item["kind"] for item in result["items"]}, {"production", "research", "extraction", "series"})
        self.assertEqual(next(item for item in result["items"] if item["kind"] == "research")["status"], "failed")
        self.assertTrue(any(item["kind"] == "skill" and item["id"] == "new-skill" for item in result["objects"]))
        self.assertNotIn(str(self.root), response.text)
        self.assertEqual(self.hashes(), before)

    def test_heartbeat_does_not_change_notification_token(self):
        first = self.get().json()["items"][0]
        with self.db.session() as session:
            row = session.get(ContentRun, "run")
            row.heartbeat_at = datetime.now(timezone.utc)
            row.updated_at = datetime.now(timezone.utc)
        second = self.get().json()["items"][0]
        self.assertEqual(first["token"], second["token"])
        with self.db.session() as session:
            session.get(ContentRun, "run").status = ContentRunStatus.APPROVED
        self.assertNotEqual(second["token"], self.get().json()["items"][0]["token"])
        before_attempt = next(item["token"] for item in self.get().json()["items"] if item["id"] == "run")
        with self.db.session() as session:
            session.add(ContentAttempt(id="retry", revision_id="revision", attempt_number=1, status=ContentAttemptStatus.FAILED))
        after_attempt = next(item["token"] for item in self.get().json()["items"] if item["id"] == "run")
        self.assertNotEqual(before_attempt, after_attempt)

    def test_moved_account_records_are_not_exposed_and_bad_json_is_counted(self):
        self.write(self.research.root / "batches/moved.json", {"id": "moved", "series_id": "series", "status": "ready",
            "snapshot": {"creator": {"id": "old-account"}}, "created_at": "2026-10-20T00:00:00Z"})
        bad = self.extractions.root / "jobs/bad/job.json"
        bad.parent.mkdir(parents=True)
        bad.write_text("{broken", encoding="utf-8")
        with self.db.session() as session:
            session.get(ContentRun, "run").input_snapshot_json = {"creator_id": "old-account"}
        result = self.get().json()
        self.assertEqual({item["kind"] for item in result["items"]}, {"series"})
        self.assertEqual(result["unreadable_records"], 1)
        self.assertEqual(bad.read_text(encoding="utf-8"), "{broken")

    def test_calibration_links_original_candidate_and_applied_state(self):
        with self.db.session() as session:
            session.add(SkillCalibration(id="calibration", request_id="request", input_digest="a" * 64,
                series_id="series", creator_id="account", skill_id="skill", base_skill_digest="b" * 64,
                job_id="calibration-job", evidence_json={}, status="applied"))
        self.write(self.extractions.root / "jobs/calibration-job/job.json", {"id": "calibration-job",
            "task_kind": "calibrate", "status": "ready", "calibration_target_name": "故事方法",
            "created_at": "2026-10-20T03:00:00Z", "revision": 2, "digest": "d" * 64})
        item = next(item for item in self.get().json()["items"] if item["kind"] == "calibration")
        self.assertEqual(item["url"], "/skills?extraction=calibration-job&calibration=calibration")
        self.assertEqual(item["status"], "applied")
        self.assertEqual(item["skill_ids"], ["skill"])

    def test_bounded_list_keeps_new_saved_skill_among_old_pending_items(self):
        for index in range(60):
            self.write(self.extractions.root / f"jobs/old-{index}/job.json", {"id": f"old-{index}", "status": "ready",
                "created_at": "2026-10-01T00:00:00Z", "revision": 1, "digest": "a" * 64})
        self.write(self.extractions.root / "jobs/new/job.json", {"id": "new", "status": "saved",
            "created_at": "2026-10-20T00:00:00Z", "skills": [{"name": "新 Skill"}]})
        result = self.get("?limit=20").json()
        self.assertEqual(len(result["items"]), 20)
        self.assertTrue(result["has_more"])
        self.assertTrue(any(item["id"] == "new" for item in result["items"]))
        self.assertEqual(self.get("?limit=101").status_code, 422)

    def test_revise_and_trial_statuses_are_projected_without_losing_ready_draft(self):
        path = self.extractions.root / "jobs/edit/job.json"
        job = {"id": "edit", "status": "ready", "operation": "revise", "revision": 1,
               "created_at": "2026-10-20T00:00:00Z", "digest": "d" * 64}
        self.write(path, job)
        def state():
            return next(item["status"] for item in self.get().json()["items"] if item["id"] == "edit")
        self.assertEqual(state(), "running")
        job.update(operation=None, error="改稿未完成，原草稿保留。")
        self.write(path, job)
        self.assertEqual(state(), "failed")
        job.update(error=None, trials=[{"id": "trial", "digest": job["digest"], "status": "failed"}])
        self.write(path, job)
        self.assertEqual(state(), "failed")
        job.update(digest="e" * 64, revision=2)
        self.write(path, job)
        self.assertEqual(state(), "ready")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["status"], "ready")


if __name__ == "__main__":
    unittest.main()
