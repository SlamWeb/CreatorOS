"""Real local SQLite/files; synthetic activity is explicit fault injection."""
import json
import os
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
import unittest

from creatoros.maintenance.state import (DB_BUSY, Layout, MaintenanceError, backup,
    inspect_idle, migration_pending, safe_path, snapshot, verify_snapshot)
from creatoros.storage import Database, ContentRepository, SkillIteration, TopicSource, upgrade_database
from creatoros.runs import ContentRunService


class MaintenanceStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed = TemporaryDirectory()
        root = Path(cls.seed.name)
        cls.seed_db = root / "seed.db"
        url = "sqlite:///" + cls.seed_db.as_posix()
        upgrade_database(url)
        db = Database(url)
        try:
            repo = ContentRepository(db)
            repo.create_creator(creator_id="owner", display_name="Isolated owner")
            repo.create_series(series_id="series", creator_id="owner", name="Series",
                description="", audience="", skill_name="knowledge-to-carousel")
            repo.add_topic(topic_id="topic", series_id="series", title="Old topic",
                           brief="Original words", source=TopicSource.MANUAL)
            ContentRunService(db, output_root=root / "outputs").create("topic")
            with db.session() as session:
                session.add(SkillIteration(id="a" * 64, request_id="old", input_digest="b" * 64,
                    creator_id="owner", series_id="series", skill_id="skill", topic_id="topic",
                    status="failed", revision=3, config_json={"old": "unchanged"},
                    state_json={"rounds": [], "calls": {}, "judge": {"criteria": [{"description": "old criterion"}]},
                                "old_score": 35}))
        finally:
            db.close()

    @classmethod
    def tearDownClass(cls):
        cls.seed.cleanup()

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "isolated.db"
        shutil.copyfile(self.seed_db, self.path)
        self.layout = Layout.from_paths(self.path, self.root / "outputs")
        self.old_image = self.layout.output / "old/image.png"
        self.old_image.parent.mkdir(parents=True)
        self.old_image.write_bytes(b"immutable fixture image bytes")
        self.job_id, self.trial_id = "c" * 64, "d" * 64

    def metadata(self, relative, value, root=None):
        path = (root or self.layout.skills) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def update_iteration(self, state, status="paused"):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE skill_iterations SET status=?,state_json=?", (status, json.dumps(state)))

    def test_check_is_read_only_and_queued_run_does_not_authorize_production(self):
        before = self.path.read_bytes()
        report = inspect_idle(self.layout)
        self.assertTrue(report["idle"], report)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.layout.control_dir.exists())
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT status FROM content_runs").fetchall(), [("queued",)])
            self.assertEqual(db.execute("SELECT count(*) FROM production_queue_items").fetchone()[0], 0)

    def test_missing_database_is_not_created(self):
        layout = Layout.from_paths(self.root / "missing.db", self.root / "out")
        self.assertFalse(inspect_idle(layout)["idle"])
        self.assertFalse(layout.database.exists())

    def test_each_database_activity_category_blocks(self):
        # Tiny schema deliberately isolates inventory semantics, not worker execution.
        for table, statuses in DB_BUSY.items():
            for status in statuses:
                with self.subTest(table=table, status=status), TemporaryDirectory() as temp:
                    layout = Layout.from_paths(Path(temp) / "probe.db", Path(temp) / "out")
                    with closing(sqlite3.connect(layout.database)) as db, db:
                        for name in DB_BUSY:
                            db.execute(f'CREATE TABLE "{name}" (id TEXT, status TEXT, state_json TEXT)')
                        db.execute(f'INSERT INTO "{table}" VALUES (?,?,?)', ("task", status, '{"rounds":[],"calls":{}}'))
                    report = inspect_idle(layout)
                    self.assertFalse(report["idle"])
                    self.assertTrue(any(item["source"] == table for item in report["busy"]), report)

    def test_auxiliary_running_queue_and_nested_requests_block(self):
        cases = [{"status": "running"}, {"status": "ready", "queue": {"state": "queued"}},
                 {"status": "ready", "operation": "trial"},
                 {"status": "ready", "trials": [{"status": "running"}]},
                 {"status": "idle", "requests": [{"status": "running"}]}]
        for index, record in enumerate(cases):
            path = self.metadata(f"extractions/jobs/{index}/job.json", record)
            self.assertFalse(inspect_idle(self.layout)["idle"], record)
            path.unlink()

    def test_other_metadata_roots_are_not_hardcoded_to_formal_data(self):
        examples = [(self.layout.skills, "jobs/install.json", "installing"),
                    (self.layout.research, "batches/research.json", "researching"),
                    (self.layout.chats, "conversation/view.json", "running"),
                    (self.layout.discussions, "records/discussion.json", "queued")]
        for root, relative, status in examples:
            path = self.metadata(relative, {"status": status}, root)
            self.assertFalse(inspect_idle(self.layout)["idle"])
            path.unlink()

    def test_corrupt_unknown_and_non_object_metadata_is_not_silently_skipped(self):
        path = self.metadata("extractions/jobs/broken/job.json", {"status": "ready"})
        for raw in ("{bad", "[]", '{"status":"surprise"}', '{"status":"ready","trials":null}'):
            path.write_text(raw, encoding="utf-8")
            self.assertFalse(inspect_idle(self.layout)["idle"], raw)
            self.assertEqual(path.read_text(encoding="utf-8"), raw)

    def test_stale_research_is_a_valid_terminal_record_and_unchanged(self):
        path = self.metadata("batches/stale.json", {"status": "stale"}, self.layout.research)
        before = snapshot(self.layout)
        self.assertTrue(inspect_idle(self.layout)["idle"])
        self.assertTrue(verify_snapshot(self.layout, before)["original_rows_unchanged"])
        self.assertEqual(path.read_text(), '{"status": "stale"}')

    def test_execution_journal_blocks_and_is_never_cleaned_up(self):
        path = self.path.with_name(self.path.name + ".execution.json")
        path.write_text("unknown surviving writer", encoding="utf-8")
        self.assertFalse(inspect_idle(self.layout)["idle"])
        self.assertEqual(path.read_text(encoding="utf-8"), "unknown surviving writer")

    def test_paused_rsi_checks_real_trial_not_stale_projection(self):
        state = {"rounds": [{"trial_job_id": self.job_id, "trial_id": self.trial_id, "status": "running"}], "calls": {}}
        self.update_iteration(state)
        self.assertFalse(inspect_idle(self.layout)["idle"], "missing trial must not authorize restart")
        self.metadata(f"extractions/jobs/{self.job_id}/job.json", {"id": self.job_id, "status": "ready",
            "trials": [{"id": self.trial_id, "status": "running"}]})
        self.assertFalse(inspect_idle(self.layout)["idle"])
        self.metadata(f"extractions/jobs/{self.job_id}/job.json", {"id": self.job_id, "status": "ready",
            "trials": [{"id": self.trial_id, "status": "completed"}]})
        self.assertTrue(inspect_idle(self.layout)["idle"])

    def test_unconfirmed_rsi_model_request_blocks_even_if_parent_failed(self):
        self.update_iteration({"rounds": [], "calls": {"judge": {"status": "inflight"}}}, "failed")
        self.assertFalse(inspect_idle(self.layout)["idle"])

    def test_backup_is_exclusive_and_preserves_full_old_rows_and_files(self):
        before = snapshot(self.layout)
        target = self.layout.control_dir / "backup/before.sqlite"
        backup(self.layout, target)
        with self.assertRaises(FileExistsError):
            backup(self.layout, target)
        self.assertTrue(verify_snapshot(self.layout, before)["original_rows_unchanged"])
        with closing(sqlite3.connect(target)) as db:
            self.assertIn("old criterion", db.execute("SELECT state_json FROM skill_iterations").fetchone()[0])
        self.assertEqual(self.old_image.read_bytes(), b"immutable fixture image bytes")

    def test_full_value_check_catches_same_row_count_changes(self):
        before = snapshot(self.layout)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE topics SET brief='changed behind the same id'")
        with self.assertRaises(MaintenanceError):
            verify_snapshot(self.layout, before)

    def test_rsi_standard_or_score_and_image_drift_cannot_pass(self):
        before = snapshot(self.layout)
        self.update_iteration({"rounds": [], "calls": {}, "old_score": 99}, "failed")
        with self.assertRaises(MaintenanceError):
            verify_snapshot(self.layout, before)
        before = snapshot(self.layout)
        self.old_image.write_bytes(b"different bytes")
        with self.assertRaises(MaintenanceError):
            verify_snapshot(self.layout, before)

    def test_added_migration_columns_are_allowed_but_old_fields_still_must_match(self):
        before = snapshot(self.layout)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("ALTER TABLE topics ADD COLUMN future_field TEXT")
        self.assertTrue(verify_snapshot(self.layout, before)["original_rows_unchanged"])

    def test_real_upgrade_allows_only_repository_migration_bookkeeping(self):
        from alembic.script import ScriptDirectory
        from creatoros.config import PROJECT_ROOT
        script = ScriptDirectory(str(PROJECT_ROOT / "migrations"))
        head = script.get_revision(script.get_current_head())
        path = self.root / "older.db"
        layout = Layout.from_paths(path, self.root / "older-output")
        url = "sqlite:///" + path.as_posix()
        upgrade_database(url, head.down_revision)
        self.assertTrue(migration_pending(layout))
        self.assertFalse(migration_pending(self.layout))
        before = snapshot(layout)
        upgrade_database(url)
        self.assertFalse(migration_pending(layout))
        self.assertTrue(verify_snapshot(layout, before)["original_rows_unchanged"])
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("UPDATE alembic_version SET version_num='unrecognized-version'")
        with self.assertRaises(MaintenanceError):
            verify_snapshot(layout, before)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("UPDATE alembic_version SET version_num=?", (head.down_revision,))
        current = snapshot(self.layout)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE alembic_version SET version_num=?", (head.down_revision,))
        with self.assertRaises(MaintenanceError):
            verify_snapshot(self.layout, current)

    def test_reference_originals_are_included_in_file_preservation(self):
        path = self.metadata("bundle/input.json", {"original": "immutable"}, self.layout.references)
        before = snapshot(self.layout)
        self.assertIn("references/bundle/input.json", before["files"])
        path.write_text("changed", encoding="utf-8")
        with self.assertRaises(MaintenanceError):
            verify_snapshot(self.layout, before)
        path.unlink()
        with self.assertRaises(MaintenanceError):
            verify_snapshot(self.layout, before)

    def test_managed_uploads_and_export_receipts_are_preserved_without_changing_layout_identity(self):
        identity = self.layout.fingerprint()
        for label, suffix in (("materials", ".materials"), ("media_exports", ".media-exports")):
            with self.subTest(root=label):
                path = self.layout.database.with_suffix(suffix) / "original" / "source.bin"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"original managed bytes")
                before = snapshot(self.layout)
                self.assertEqual(before["layout"], identity)
                self.assertEqual(self.layout.fingerprint(), identity)
                self.assertIn(f"{label}/original/source.bin", before["files"])
                self.assertTrue(verify_snapshot(self.layout, before)["original_rows_unchanged"])
                path.write_bytes(b"changed managed bytes")
                with self.assertRaises(MaintenanceError):
                    verify_snapshot(self.layout, before)
                path.unlink()
                with self.assertRaises(MaintenanceError):
                    verify_snapshot(self.layout, before)
    @unittest.skipUnless(os.name == "nt", "Windows Junction regression")
    def test_windows_junction_and_linked_parent_are_rejected_on_python310(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.root / "junction"
        subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(outside)],
                       check=True, capture_output=True)
        try:
            with self.assertRaises(MaintenanceError):
                safe_path(link)
            with self.assertRaises(MaintenanceError):
                safe_path(link / "missing/instance.json")
            self.assertEqual(list(outside.iterdir()), [])
        finally:
            # Remove only this directory link, never recurse into its target.
            link.rmdir()

    def test_custom_database_maintenance_tokens_and_backups_are_git_ignored(self):
        from creatoros.config import PROJECT_ROOT
        for relative in ("probe.db.maintenance/instance.json", "probe.db.maintenance/evidence/before.sqlite"):
            result = subprocess.run(["git", "check-ignore", "--no-index", relative],
                                    cwd=PROJECT_ROOT, capture_output=True)
            self.assertEqual(result.returncode, 0, relative)

    def test_backup_outside_maintenance_directory_is_rejected(self):
        with self.assertRaises(MaintenanceError):
            backup(self.layout, self.root / "outside.sqlite")
        self.assertFalse((self.root / "outside.sqlite").exists())


if __name__ == "__main__":
    unittest.main()
