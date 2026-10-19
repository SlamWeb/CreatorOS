"""Real local lock/journal concurrency and crash injection; no external models."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch

from creatoros.runs.ownership import ExecutionOwnershipError, LocalExecutionGuard
from creatoros.storage import Database


class ConcurrentExecutionGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Database(f"sqlite:///{(Path(self.temporary.name) / 'guard.db').as_posix()}")
        self.addCleanup(self.database.close)
        self.guard = LocalExecutionGuard(self.database)

    def begin(self, number=0, guard=None):
        (guard or self.guard).begin(owner_id=f"owner-{number}", run_id=f"run-{number}",
                                    attempt_id=f"attempt-{number}")

    def payload(self):
        return json.loads(self.guard.journal.read_text(encoding="utf-8"))

    def write_payload(self, value):
        self.guard.journal.write_text(json.dumps(value), encoding="utf-8")

    def test_five_parallel_owners_callbacks_and_finish_are_isolated(self):
        barrier = Barrier(5)
        with self.guard:
            def execute(number):
                self.begin(number)
                barrier.wait(timeout=5)
                self.guard.process_started(f"owner-{number}", {"pid": number + 100, "created_at": 1.0})
                barrier.wait(timeout=5)
                snapshot = self.payload()
                self.assertEqual(len(snapshot["executions"]), 5)
                for record in snapshot["executions"]:
                    expected = int(record["owner_id"].split("-")[1])
                    self.assertEqual(record["process"]["pid"], expected + 100)
                    self.assertEqual(record["phase"], "process_running")
                barrier.wait(timeout=5)
                self.guard.process_stopped(f"owner-{number}")
                self.guard.finish(f"owner-{number}")
            with ThreadPoolExecutor(max_workers=5) as pool:
                list(pool.map(execute, range(5)))
            self.assertFalse(self.guard.journal.exists())
            self.guard.assert_clean()

    def test_single_format_and_multi_to_single_preserve_other_record(self):
        with self.guard:
            self.begin(0)
            self.assertNotIn("executions", self.payload())
            self.begin(1)
            self.assertEqual(self.payload()["schema_version"], 2)
            self.guard.process_started("owner-1", {"pid": 101})
            self.guard.finish("owner-0")
            self.assertEqual(self.payload()["owner_id"], "owner-1")
            self.assertEqual(self.payload()["process"], {"pid": 101})
            self.guard.process_stopped("owner-1")
            self.guard.finish("owner-1")

    def test_assert_clean_stays_strict_during_registered_execution(self):
        with self.guard:
            self.guard.assert_clean()
            self.begin()
            with self.assertRaises(ExecutionOwnershipError):
                self.guard.assert_clean()
            self.guard.assert_dispatchable()
            self.guard.finish("owner-0")

    def test_duplicate_owner_run_and_attempt_are_rejected_without_mutation(self):
        with self.guard:
            self.begin()
            original = self.guard.journal.read_bytes()
            for owner, run, attempt in (("owner-0", "run-new", "attempt-new"),
                                        ("owner-new", "run-0", "attempt-new"),
                                        ("owner-new", "run-new", "attempt-0")):
                with self.subTest(owner=owner, run=run, attempt=attempt):
                    with self.assertRaises(ExecutionOwnershipError):
                        self.guard.begin(owner_id=owner, run_id=run, attempt_id=attempt)
                    self.assertEqual(self.guard.journal.read_bytes(), original)
            self.guard.finish("owner-0")

    def test_unknown_callback_or_finish_cannot_overwrite_records(self):
        with self.guard:
            self.begin()
            original = self.guard.journal.read_bytes()
            for action in (lambda: self.guard.process_started("unknown", {"pid": 2}),
                           lambda: self.guard.process_stopped("unknown"),
                           lambda: self.guard.finish("unknown")):
                with self.assertRaises(ExecutionOwnershipError):
                    action()
                self.assertEqual(self.guard.journal.read_bytes(), original)
            self.guard.finish("owner-0")

    def test_running_owner_cannot_be_finished_or_erase_other_owner(self):
        with self.guard:
            self.begin(0)
            self.begin(1)
            self.guard.process_started("owner-0", {"pid": 100})
            original = self.guard.journal.read_bytes()
            with self.assertRaises(ExecutionOwnershipError):
                self.guard.finish("owner-0")
            self.assertEqual(self.guard.journal.read_bytes(), original)
            self.guard.process_stopped("owner-0")
            self.guard.finish("owner-0")
            self.guard.finish("owner-1")

    def test_legacy_dirty_journal_blocks_new_dispatch_but_explicit_finish_works(self):
        legacy = {"owner_id": "old", "run_id": "run-old", "attempt_id": "attempt-old",
                  "phase": "claimed", "host_pid": os.getpid()}
        self.write_payload(legacy)
        with self.guard:
            for action in (self.guard.assert_clean, self.guard.assert_dispatchable,
                           lambda: self.begin(), lambda: self.guard.process_stopped("old")):
                with self.assertRaises(ExecutionOwnershipError):
                    action()
                self.assertEqual(self.payload(), legacy)
            self.guard.finish("old")
            self.guard.assert_clean()

    def test_reacquired_same_guard_cannot_adopt_previous_owners(self):
        with self.guard:
            self.begin()
        with self.guard:
            with self.assertRaises(ExecutionOwnershipError):
                self.guard.assert_dispatchable()
            self.guard.finish("owner-0")

    def test_dirty_multi_owner_restart_rejects_dispatch_and_manual_partial_finish(self):
        with self.guard:
            self.begin(0)
            self.begin(1)
        restarted = LocalExecutionGuard(self.database)
        original = self.guard.journal.read_bytes()
        with restarted:
            for action in (restarted.assert_clean, restarted.assert_dispatchable,
                           lambda: self.begin(2, restarted), lambda: restarted.finish("owner-0")):
                with self.assertRaises(ExecutionOwnershipError):
                    action()
                self.assertEqual(self.guard.journal.read_bytes(), original)

    def test_tampered_or_deleted_registered_journal_is_fail_closed(self):
        with self.guard:
            self.begin()
            original = self.payload()
            changed = dict(original, phase="process_stopped")
            for replacement in (changed, {}, {"schema_version": 99, "executions": [original]},
                                {"schema_version": 2, "executions": [original, original]}):
                with self.subTest(replacement=replacement):
                    self.write_payload(replacement)
                    before = self.guard.journal.read_bytes()
                    for action in (self.guard.assert_dispatchable, lambda: self.begin(1),
                                   lambda: self.guard.process_stopped("owner-0"),
                                   lambda: self.guard.finish("owner-0")):
                        with self.assertRaises(ExecutionOwnershipError):
                            action()
                        self.assertEqual(self.guard.journal.read_bytes(), before)
            self.guard.journal.unlink()
            for action in (self.guard.assert_clean, self.guard.assert_dispatchable,
                           lambda: self.begin(1), lambda: self.guard.finish("owner-0")):
                with self.assertRaises(ExecutionOwnershipError):
                    action()
            self.write_payload(original)
            self.guard.finish("owner-0")

    def test_invalid_journals_are_not_overwritten(self):
        with self.guard:
            for raw in ("{broken", "[]", '{"schema_version":2,"executions":[]}',
                        '{"owner_id":"old"}'):
                self.guard.journal.write_text(raw, encoding="utf-8")
                for action in (self.guard.assert_dispatchable, lambda: self.begin()):
                    with self.assertRaises(ExecutionOwnershipError):
                        action()
                    self.assertEqual(self.guard.journal.read_text(encoding="utf-8"), raw)

    def test_failed_journal_write_does_not_register_owner(self):
        with self.guard:
            with patch.object(self.guard, "_write", side_effect=OSError("injected disk failure")):
                with self.assertRaises(OSError):
                    self.begin()
            self.guard.assert_clean()
            self.begin()
            self.guard.finish("owner-0")

    def test_database_os_lock_still_excludes_another_guard(self):
        other = LocalExecutionGuard(self.database)
        with self.guard:
            with self.assertRaises(ExecutionOwnershipError):
                other.__enter__()
        with other:
            other.assert_clean()

    def test_all_operations_require_os_lock(self):
        for action in (self.guard.assert_clean, self.guard.assert_dispatchable,
                       lambda: self.begin(), lambda: self.guard.process_stopped("owner-0"),
                       lambda: self.guard.finish("owner-0")):
            with self.assertRaises(ExecutionOwnershipError):
                action()


if __name__ == "__main__":
    unittest.main()
