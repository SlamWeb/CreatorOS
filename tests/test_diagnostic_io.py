"""Filesystem faults, real Windows sharing locks and isolated HTTP; no paid calls."""
import asyncio
import base64
import errno
import json
import os
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

from creatoros.integrations.atomic_file import atomic_write_text
from creatoros.integrations.extraction_activity import ExtractionActivity
from creatoros.integrations.producer_skills import ProducerSkillCatalog, SkillInstallService, _write
from creatoros.integrations.production_progress import (
    OBSERVATION_WARNING, ProgressWriter, collect_observed_turn, read_observation_warning,
)
from creatoros.integrations.skill_extraction import SkillExtractionService, extraction_failure
from creatoros.runs import ContentRunService
from creatoros.storage import Database, upgrade_database
from creatoros.web import create_app
from tests.agent_studio_support import serve
from tests.smoke_production_progress import FakeTurn, assistant_item, completed_item, token_update, turn_completed


class DiagnosticIOTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="diagnostic-io-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def events(self, status="completed", error=None):
        return [completed_item(assistant_item("final", "合法最终回复", "final_answer")),
                token_update(12), turn_completed(status, error)]

    def test_atomic_retry_is_bounded_and_preserves_old_file(self):
        target = self.root / "record.json"
        target.write_text("old", encoding="utf-8")
        real_replace = os.replace
        seen = []
        failure = PermissionError("private-path-or-secret")
        failure.winerror = 5
        def once(source, destination):
            seen.append(source)
            if len(seen) == 1:
                raise failure
            real_replace(source, destination)
        with patch("creatoros.integrations.atomic_file.os.replace", side_effect=once):
            atomic_write_text(target, "new")
        self.assertEqual(target.read_text(), "new")
        self.assertEqual(len(seen), 2)
        with patch("creatoros.integrations.atomic_file.os.replace", side_effect=failure) as replace:
            with self.assertRaises(PermissionError):
                atomic_write_text(target, "not-published")
        self.assertEqual(replace.call_count, 4)
        self.assertEqual(target.read_text(), "new")
        self.assertFalse(list(self.root.glob("*.tmp")))
        with patch("creatoros.integrations.atomic_file.os.replace", side_effect=OSError(errno.ENOSPC, "disk full")) as replace:
            with self.assertRaises(OSError):
                atomic_write_text(target, "not-published")
        self.assertEqual(replace.call_count, 1, "disk full is not a Windows sharing conflict")

    @unittest.skipUnless(os.name == "nt", "real sharing lock needs Windows")
    def test_real_windows_sharing_lock_recovers_then_degrades_without_killing_stream(self):
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        writer = ProgressWriter(self.root, "production")
        target = self.root / "production_progress.json"
        def hold():
            handle = kernel.CreateFileW(str(target), 0x80000000, 3, None, 3, 0x80, None)
            self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
            return handle  # FILE_SHARE_READ|WRITE, deliberately no FILE_SHARE_DELETE.
        handle = hold()
        denied = threading.Event()
        released = threading.Event()
        def release():
            denied.wait(2)
            kernel.CloseHandle(handle)
            released.set()
        thread = threading.Thread(target=release)
        thread.start()
        original = os.replace
        def observed_replace(source, destination):
            try:
                original(source, destination)
            except OSError:
                denied.set()
                raise
        try:
            with patch("creatoros.integrations.atomic_file.os.replace", side_effect=observed_replace):
                writer.save()
        finally:
            denied.set()
            thread.join(3)
        self.assertTrue(released.is_set() and denied.is_set())
        self.assertFalse(writer.diagnostic_failures, "brief file sharing contention should recover")
        handle = hold()
        try:
            turn = FakeTurn(self.events())
            result = asyncio.run(collect_observed_turn(turn, writer))
            writer.finish("completed")
            self.assertEqual(result.final_response, "合法最终回复")
            self.assertEqual(turn.stream_calls, 1)
            self.assertEqual(turn.run_calls, 0)
            self.assertEqual(read_observation_warning(self.root), OBSERVATION_WARNING)
            self.assertEqual(json.loads(target.read_text())["status"], "running")
        finally:
            kernel.CloseHandle(handle)
        writer.save()
        self.assertEqual(json.loads(target.read_text())["status"], "completed")
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_usage_trace_and_public_io_do_not_change_result_or_mask_sdk_error(self):
        progress = ProgressWriter(self.root, "production")
        original = os.replace
        def no_usage(source, target):
            if Path(target).name == "production_usage.json":
                raise OSError(errno.ENOSPC, "PRIVATE_SECRET")
            original(source, target)
        def broken_observer(event):
            raise PermissionError("PRIVATE_SECRET")
        with patch("creatoros.integrations.atomic_file.os.replace", side_effect=no_usage), \
             patch.object(progress, "_append_trace", side_effect=PermissionError("PRIVATE_SECRET")):
            result = asyncio.run(collect_observed_turn(FakeTurn(self.events()), progress, broken_observer))
        self.assertEqual(result.usage.total.input_tokens, 12)
        self.assertEqual(progress.diagnostic_failures, {"usage", "metadata_trace", "public_activity"})
        with patch.object(progress, "_append_trace", side_effect=PermissionError("PRIVATE_SECRET")):
            with self.assertRaisesRegex(RuntimeError, "REAL_SDK_ERROR"):
                asyncio.run(collect_observed_turn(FakeTurn(self.events("failed", {"message": "REAL_SDK_ERROR"})), progress))
        self.assertNotIn("PRIVATE_SECRET", (self.root / "observation_health.json").read_text())

    def test_non_io_tool_guard_and_receipt_write_errors_still_propagate(self):
        progress = ProgressWriter(self.root, "production")
        def forbidden_tool(event):
            raise RuntimeError("tool guard must stop execution")
        with self.assertRaisesRegex(RuntimeError, "tool guard"):
            asyncio.run(collect_observed_turn(FakeTurn(self.events()), progress, forbidden_tool))
        with patch("creatoros.integrations.worker_protocol.record_turn", side_effect=PermissionError("receipt is authoritative")):
            with self.assertRaises(PermissionError):
                asyncio.run(collect_observed_turn(FakeTurn(self.events()), progress))

    def test_failure_reporting_does_not_leave_dead_worker_running(self):
        async def fail(*args):
            raise ValueError("invalid real draft")
        service = SkillExtractionService(ProducerSkillCatalog(self.root / "catalog"), fail)
        service.start()
        self.addCleanup(service.shutdown)
        with patch.object(ExtractionActivity, "finish", side_effect=PermissionError("report locked")):
            job = service.submit("failure-report", [], "mind", "", source_text="示例文案")
            service.worker.join(5)
        self.assertFalse(service.worker.is_alive())
        recorded = service.get(job["id"])
        self.assertEqual(recorded["status"], "failed")
        self.assertIsNone(recorded["operation"])
        self.assertEqual(recorded["revision"], 0)

    def test_warning_http_read_is_durable_read_only_and_distinct_from_failure(self):
        url = f"sqlite:///{(self.root / 'test.db').as_posix()}"
        upgrade_database(url)
        with ExitStack() as cleanup:
            db = Database(url)
            cleanup.callback(db.close)
            catalog = ProducerSkillCatalog(self.root / "catalog")
            service = SkillExtractionService(catalog)
            app = create_app(database=db, run_service=ContentRunService(db, output_root=self.root / "outputs"),
                             skill_install_service=SkillInstallService(catalog), skill_extraction_service=service)
            with serve(app) as base, httpx.Client(base_url=base, trust_env=False, timeout=5) as client:
                identifier = "a" * 64
                directory = service._path("jobs", identifier)
                directory.mkdir(parents=True)
                _write(directory / "job.json", {"id": identifier, "status": "running", "mode": "visual",
                    "operation": "extract", "skills": [], "uploads": [], "saved_skills": [], "digest": None})
                writer = ProgressWriter(directory, "production")
                writer.warning("progress", PermissionError("PRIVATE_SECRET"))
                before = (directory / "job.json").read_bytes()
                for _ in range(2):
                    response = client.get(f"/api/skill-extractions/{identifier}")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["status"], "running")
                    self.assertEqual(response.json()["observation_warning"], OBSERVATION_WARNING)
                    self.assertNotIn("PRIVATE_SECRET", response.text)
                self.assertEqual((directory / "job.json").read_bytes(), before)
                self.assertEqual(extraction_failure(PermissionError("secret"))[0], "local_file_io")
                legacy = "[WinError 5] 拒绝访问。: 'private.tmp' -> 'private.json'"
                self.assertEqual(extraction_failure(RuntimeError(legacy))[0], "local_file_io")

    def test_revision_and_trial_reporting_failures_retain_draft_and_terminal_state(self):
        from tests.smoke_skill_workbench import controlled, image_bytes, revise
        class FailedProducer:
            def produce_to(self, **request):
                raise ValueError("actual production failure")
        service = SkillExtractionService(ProducerSkillCatalog(self.root / "catalog"), controlled,
                                         reviser=revise, producer_factory=FailedProducer)
        service.start()
        self.addCleanup(service.shutdown)
        upload = service.upload("example.png", base64.b64encode(image_bytes()).decode())
        job = service.submit("workbench", [upload["id"]], "single", "")
        service.worker.join(5)
        draft = service.get(job["id"])
        original_write = Path.write_text
        def unavailable_error_file(path, *args, **kwargs):
            if path.name == "error.txt":
                raise PermissionError("error report denied")
            return original_write(path, *args, **kwargs)
        with patch.object(Path, "write_text", unavailable_error_file), \
             patch.object(ExtractionActivity, "finish", side_effect=PermissionError("activity denied")):
            service.revise(job["id"], "revise-failure", draft["digest"], "fail")
            service.worker.join(5)
            failed_revision = service.get(job["id"])
            self.assertIsNone(failed_revision["operation"])
            self.assertEqual(failed_revision["digest"], draft["digest"])
            self.assertEqual(failed_revision["status"], "ready")
            self.assertIn("改稿未完成", failed_revision["error"])
            service.trial(job["id"], "trial-failure", draft["digest"], "example topic")
            service.worker.join(5)
        failed_trial = service.get(job["id"])
        self.assertFalse(service.worker.is_alive())
        self.assertIsNone(failed_trial["operation"])
        self.assertEqual(failed_trial["trials"][0]["status"], "failed")
        self.assertEqual(failed_trial["digest"], draft["digest"])


if __name__ == "__main__":
    unittest.main()
