"""Real isolated HTTP and SQLite. Concurrency flags are deliberate fault injection."""
import asyncio
from contextlib import contextmanager, nullcontext
import os
from pathlib import Path
import subprocess
import threading
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from filelock import FileLock, Timeout

from creatoros.maintenance.control import MaintenanceController, MaintenanceMiddleware
from creatoros.maintenance.state import Layout, MaintenanceError
from creatoros.runs import ContentRunService
from creatoros.storage import Database, upgrade_database
from creatoros.web import create_app
from tests.agent_studio_support import serve


class MaintenanceControlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.url = "sqlite:///" + (self.root / "isolated.db").as_posix()
        upgrade_database(self.url)
        self.db = Database(self.url)
        self.addCleanup(self.db.close)
        self.runs = ContentRunService(self.db, output_root=self.root / "outputs")
        self.app = create_app(database=self.db, run_service=self.runs, defer_background_start=True)
        self.layout = Layout.from_paths(self.root / "isolated.db", self.runs.output_root)
        self.shutdown_calls = []
        self.controller = MaintenanceController(self.app, self.layout, port=0, held=True,
            shutdown_callback=lambda: self.shutdown_calls.append(True))
        self.app.state.maintenance = self.controller
        self.app.add_middleware(MaintenanceMiddleware, controller=self.controller)
        self.controller.register()
        self.addCleanup(self.controller.close)

    @contextmanager
    def client(self):
        with serve(self.app) as base, httpx.Client(base_url=base, timeout=10, trust_env=False) as client:
            yield client

    def call(self, client, action, *, data=None, headers=None):
        return client.post("/api/_maintenance", json=data or {
            "action": action, "instance_id": self.controller.instance_id}, headers={
                "X-CreatorOS-Maintenance": self.controller.token, **(headers or {})})

    def test_deferred_start_and_shutdown_do_not_recover_or_start_workers(self):
        before = self.layout.database.read_bytes()
        with self.client() as client:
            response = self.call(client, "status")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(response.json()["workers_started"])
            self.assertTrue(response.json()["held"])
            self.assertNotIn(self.controller.token, response.text)
            self.assertEqual(client.get("/api/health").status_code, 200)
            self.assertEqual(client.post("/api/creators", json={"display_name": "blocked"}).status_code, 503)
            self.assertEqual(self.call(client, "shutdown").status_code, 200)
            self.assertEqual(self.shutdown_calls, [True])
            self.assertIsNone(self.app.state.executor._pool)
            self.assertIsNone(self.app.state.skill_iterations.worker)
            self.assertIsNone(self.app.state.feedback._loop_task)
        self.assertEqual(self.layout.database.read_bytes(), before)
        self.assertFalse(self.runs.guard.lock.is_locked)

    def test_auth_origin_agent_identity_content_type_and_fields_fail_closed(self):
        with self.client() as client:
            for headers in ({"Origin": "http://127.0.0.1:8765"},
                            {"X-CreatorOS-Agent-Session": "source"},
                            {"X-CreatorOS-Origin": "agent"},
                            {"X-CreatorOS-Maintenance": "wrong"}):
                self.assertEqual(self.call(client, "status", headers=headers).status_code, 403)
            self.assertEqual(self.call(client, "status", headers={"Content-Type": "text/plain"}).status_code, 415)
            for data in ({"action": "status", "instance_id": self.controller.instance_id, "extra": True},
                         {"action": "unknown", "instance_id": self.controller.instance_id},
                         {"action": [], "instance_id": self.controller.instance_id}):
                self.assertEqual(self.call(client, "status", data=data).status_code, 422)
            for identity in ("other", "不是当前实例"):
                response = self.call(client, "status", data={"action": "status", "instance_id": identity})
                self.assertEqual(response.status_code, 409)

    def test_live_chat_blocks_begin_without_closing_admission(self):
        self.controller.held = False
        self.app.state.chat.active["in-progress"] = {"status": "running"}
        with self.client() as client:
            response = self.call(client, "begin")
            self.assertEqual(response.status_code, 409, response.text)
            self.assertFalse(self.controller.held)
        self.app.state.chat.active.clear()

    def test_each_live_channel_and_http_write_blocks_begin(self):
        with self.client() as client:
            channels = [(self.controller, "inflight_writes", 1),
                        (self.app.state.executor, "_active", {"run": True}),
                        (self.app.state.skill_extractions, "active_id", "job"),
                        (self.app.state.skill_extractions, "_draining", True),
                        (self.app.state.topic_research, "active_batch_id", "research"),
                        (self.app.state.skill_installs, "_draining", True),
                        (self.app.state.materials, "_analysis_jobs", {("creator", "music"): {"status": "running"}}),
                        (self.app.state.publishing, "_foreground_waiters", 1)]
            for target, name, value in channels:
                with self.subTest(channel=name), patch.object(target, name, value):
                    self.controller.held = False
                    response = self.call(client, "begin")
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertFalse(self.controller.held)
                    self.assertEqual(self.shutdown_calls, [])

    def test_worker_lock_contention_blocks_instead_of_waiting(self):
        acquired, release = threading.Event(), threading.Event()
        def hold():
            with self.app.state.skill_extractions.lock:
                acquired.set()
                release.wait(10)
        thread = threading.Thread(target=hold)
        thread.start()
        try:
            self.assertTrue(acquired.wait(2))
            with self.client() as client:
                self.controller.held = False
                response = self.call(client, "begin")
                self.assertEqual(response.status_code, 409, response.text)
                self.assertIn("worker_state_locked", response.json()["info"]["busy"])
        finally:
            release.set()
            thread.join(2)

    def test_an_accepted_http_mutation_finishes_before_begin_can_close_admission(self):
        entered, finish = threading.Event(), threading.Event()
        @self.app.post("/api/_test_slow_mutation")
        async def slow_mutation():
            entered.set()
            while not finish.is_set():
                await asyncio.sleep(.01)
            return {"finished": True}
        self.controller.held = False
        with self.client() as client:
            outcomes = []
            thread = threading.Thread(target=lambda: outcomes.append(
                client.post("/api/_test_slow_mutation", json={})))
            thread.start()
            try:
                self.assertTrue(entered.wait(3))
                response = self.call(client, "begin")
                self.assertEqual(response.status_code, 409, response.text)
                self.assertFalse(self.controller.held)
            finally:
                finish.set()
                thread.join(3)
            self.assertEqual(outcomes[0].status_code, 200)
            self.assertEqual(self.call(client, "begin").status_code, 200)
            self.assertTrue(self.controller.held)
            self.assertEqual(client.post("/api/_test_slow_mutation", json={}).status_code, 503)

    def test_release_starts_once_reopens_writes_and_preserves_read_only_status(self):
        with self.client() as client:
            self.assertEqual(self.call(client, "release").status_code, 200)
            self.assertTrue(self.app.state.background_services_started)
            self.assertFalse(self.controller.held)
            worker = self.app.state.executor._dispatcher
            self.assertEqual(self.call(client, "release").status_code, 200)
            self.assertIs(worker, self.app.state.executor._dispatcher)
            response = client.post("/api/creators", json={"display_name": "isolated"})
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(self.call(client, "shutdown").status_code, 409)
            self.assertEqual(self.call(client, "begin").status_code, 200)
            self.assertEqual(self.call(client, "shutdown").status_code, 200)

    def test_failures_hold_admission_and_busy_shutdown_never_calls_callback(self):
        with self.client() as client:
            with patch.object(self.app.state, "start_background_services", side_effect=RuntimeError("injected")):
                self.assertEqual(self.call(client, "release").status_code, 503)
                self.assertTrue(self.controller.held)
            self.controller.shutdown_callback = lambda: (_ for _ in ()).throw(RuntimeError("injected"))
            self.assertEqual(self.call(client, "shutdown").status_code, 503)
            self.assertTrue(self.controller.held)
            self.controller.shutdown_callback = lambda: self.shutdown_calls.append(True)
            self.app.state.chat.active["late"] = {}
            self.assertEqual(self.call(client, "shutdown").status_code, 409)
            self.assertFalse(self.shutdown_calls)
            self.app.state.chat.active.clear()

    def test_owner_lock_prevents_second_receipt_and_close_only_removes_own(self):
        second = MaintenanceController(self.app, self.layout, port=1)
        before = self.layout.instance_file.read_bytes()
        with self.assertRaises(Timeout):
            second.register()
        self.assertEqual(self.layout.instance_file.read_bytes(), before)
        self.controller.close()
        self.assertFalse(self.layout.instance_file.exists())

    def _assert_main_blocked_before_migration(self):
        from creatoros.web import __main__ as launcher
        argv = ["creatoros.web", "--database-url", self.url,
                "--output-root", str(self.runs.output_root), "--port", "8899"]
        with patch("sys.argv", argv), patch.object(launcher, "upgrade_database") as migrate:
            with self.assertRaises(SystemExit) as raised:
                launcher.main()
            self.assertEqual(raised.exception.code, 2)
            migrate.assert_not_called()

    def test_main_rejects_registered_host_before_migration(self):
        self._assert_main_blocked_before_migration()

    def test_main_rejects_legacy_execution_owner_before_migration(self):
        self.controller.close()
        with self.runs.guard:
            self._assert_main_blocked_before_migration()

    def test_registration_uses_exclusive_random_temporary_and_preserves_other_files(self):
        self.controller.close()
        original_tmp = self.layout.instance_file.with_suffix(".tmp")
        original_tmp.write_text("unrelated previous temporary", encoding="utf-8")
        collision = self.layout.control_dir / ".instance-collision.tmp"
        collision.write_text("existing file must not be overwritten", encoding="utf-8")
        previous = b'{"instance_id":"previous"}'
        self.layout.instance_file.write_bytes(previous)
        controller = MaintenanceController(self.app, self.layout, port=0)
        with patch("creatoros.maintenance.control.uuid4", return_value=SimpleNamespace(hex="collision")):
            with self.assertRaises(FileExistsError):
                controller.register()
        self.assertEqual(self.layout.instance_file.read_bytes(), previous)
        self.assertEqual(collision.read_text(encoding="utf-8"), "existing file must not be overwritten")
        with FileLock(str(self.layout.control_dir / "owner.lock"), timeout=0):
            pass
        controller.register()
        self.assertNotEqual(self.layout.instance_file.read_bytes(), previous)
        self.assertEqual(original_tmp.read_text(encoding="utf-8"), "unrelated previous temporary")
        self.assertEqual(list(self.layout.control_dir.glob(".instance-*.tmp")), [collision])
        controller.close()

    def test_registration_failure_removes_only_its_created_temporary_and_releases_lock(self):
        self.controller.close()
        self.layout.instance_file.write_text('{"instance_id":"previous"}', encoding="utf-8")
        before = self.layout.instance_file.read_bytes()
        controller = MaintenanceController(self.app, self.layout, port=0)
        with patch("creatoros.maintenance.control.json.dump", side_effect=OSError("injected write failure")):
            with self.assertRaises(OSError):
                controller.register()
        self.assertEqual(self.layout.instance_file.read_bytes(), before)
        self.assertEqual(list(self.layout.control_dir.glob(".instance-*.tmp")), [])
        with FileLock(str(self.layout.control_dir / "owner.lock"), timeout=0):
            pass

    def test_registration_refuses_linked_receipt_and_owner_paths(self):
        self.controller.close()
        target = self.root / "linked-target"
        target.mkdir()
        marker = target / "preserve.txt"
        marker.write_text("unrelated data", encoding="utf-8")
        for name in ("instance.json", "owner.lock", ".instance-linked.tmp"):
            with self.subTest(path=name):
                path = self.layout.control_dir / name
                path.unlink(missing_ok=True)
                if os.name == "nt":
                    subprocess.run(["cmd", "/c", "mklink", "/J", str(path), str(target)],
                                   capture_output=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    path.symlink_to(target, target_is_directory=True)
                try:
                    controller = MaintenanceController(self.app, self.layout, port=0)
                    temporary_name = (patch("creatoros.maintenance.control.uuid4", return_value=SimpleNamespace(hex="linked"))
                                      if name.endswith(".tmp") else nullcontext())
                    with temporary_name, self.assertRaises(MaintenanceError):
                        controller.register()
                    self.assertEqual(marker.read_text(encoding="utf-8"), "unrelated data")
                finally:
                    path.rmdir() if os.name == "nt" else path.unlink()

    def test_main_disposes_database_before_releasing_owner_even_on_failure(self):
        from creatoros.web import __main__ as launcher
        self.controller.close()
        original_close = Database.close
        original_control_close = MaintenanceController.close
        for dispose_failure in (False, True):
            with self.subTest(dispose_failure=dispose_failure):
                order = []
                def close_database(database):
                    original_close(database)
                    order.append("database")
                    if dispose_failure:
                        raise RuntimeError("injected dispose failure")
                def close_controller(controller):
                    self.assertEqual(order, ["database"])
                    self.assertTrue(controller.owner.is_locked)
                    original_control_close(controller)
                    order.append("owner")
                argv = ["creatoros.web", "--database-url", self.url,
                        "--output-root", str(self.runs.output_root), "--port", "8899"]
                with patch("sys.argv", argv), patch.object(Database, "close", close_database), \
                     patch.object(MaintenanceController, "close", close_controller), \
                     patch.object(launcher.StudioServer, "run", side_effect=RuntimeError("injected serve failure")):
                    with self.assertRaises(RuntimeError):
                        launcher.main()
                self.assertEqual(order, ["database", "owner"])
                self.assertFalse(self.layout.instance_file.exists())
                with FileLock(str(self.layout.control_dir / "owner.lock"), timeout=0):
                    pass

    def test_feedback_does_not_start_remote_job_while_held(self):
        async def probe():
            with patch.object(self.app.state.feedback, "_collect") as collect:
                self.app.state.feedback._start_job("account", automatic=True)
                self.assertEqual(self.app.state.feedback._jobs, {})
                collect.assert_not_called()
        asyncio.run(probe())


if __name__ == "__main__":
    unittest.main()
