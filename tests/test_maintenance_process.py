"""Actual local command -> managed HTTP -> new process, never formal data/models."""
import json
import os
from contextlib import closing
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from tempfile import TemporaryDirectory
import unittest
from urllib.request import urlopen

from creatoros.config import PROJECT_ROOT
from creatoros.maintenance.__main__ import ControlError, control, managed, port_open, read_json, wait_closed
from creatoros.maintenance.state import Layout, snapshot, verify_snapshot
from tests import test_maintenance_state as fixtures


def fixture_environment(root, database, *, source=None):
    """Prevent inherited service settings or credentials reaching child processes."""
    source = os.environ if source is None else source
    system_names = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "SYSTEMDRIVE",
                    "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "PROCESSOR_ARCHITECTURE",
                    "NUMBER_OF_PROCESSORS", "OS"}
    environment = {name: value for name, value in source.items() if name.upper() in system_names}
    profile = root / "empty-profile"
    temporary = root / "isolated-temp"
    environment.update(
        DATABASE_URL="sqlite:///" + database.as_posix(), PYTHON_DOTENV_DISABLED="1",
        PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
        HOME=str(profile), USERPROFILE=str(profile), APPDATA=str(profile / "AppData" / "Roaming"),
        LOCALAPPDATA=str(profile / "AppData" / "Local"), CODEX_HOME=str(root / "empty-codex-home"),
        TEMP=str(temporary), TMP=str(temporary), TMPDIR=str(temporary),
        CREATOROS_XHS_MCP_URL="http://127.0.0.1:1/mcp",
        CREATOROS_XHS_MCP_TOKEN_FILE=str(root / "nonexistent-platform-token"),
        PERSONCLONE_BASE_URL="http://127.0.0.1:1", ZHIHU_OPENAPI_BASE_URL="http://127.0.0.1:1",
    )
    return environment


def close_test_instance(layout, port, *, timeout=15):
    """Retry until the loop deadline; network calls and wait_closed have their own timeouts."""
    original = read_json(layout.instance_file)
    deadline = time.monotonic() + timeout
    trace = []
    while time.monotonic() < deadline:
        receipt, status = managed(layout, port)
        if (receipt != original or receipt.get("port") != port
                or status.get("instance_id") != original.get("instance_id")
                or status.get("pid") != original.get("pid")):
            raise RuntimeError("测试实例身份或端口已改变，停止清理。")
        if type(status.get("held")) is not bool or type(status.get("idle")) is not bool:
            raise RuntimeError("测试实例状态无效，停止清理。")
        trace.append({"held": status.get("held"), "idle": status.get("idle"),
                      "busy": status.get("info", {}).get("busy")})
        if status.get("idle") is not True:
            time.sleep(.1)
            continue
        action = "shutdown" if status.get("held") is True else "begin"
        try:
            control(receipt, action)
        except ControlError as error:
            # The production client intentionally hides the 409 body. A fresh
            # authenticated status/identity check is mandatory before retrying.
            if str(error) != f"受管服务拒绝 {action}（HTTP 409）；未强制停止进程。":
                raise
            trace.append({"rejected": action, "http": 409})
            time.sleep(.1)
            continue
        if action == "shutdown":
            wait_closed(layout, port, 30, receipt["pid"])
            return trace
    raise RuntimeError(f"测试实例未能安全关闭，未强制终止。状态记录：{trace}")


class MaintenanceProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from creatoros.web.launcher import ensure_studio_build
        # The clean CI checkout has no dist yet. Build before timing the
        # replacement's bounded startup, not inside its 40-second handoff.
        ensure_studio_build(PROJECT_ROOT)
        fixtures.MaintenanceStateTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.MaintenanceStateTests.tearDownClass()

    def setUp(self):
        temporary_root = PROJECT_ROOT / "tmp"
        temporary_root.mkdir(exist_ok=True)
        self.temporary = TemporaryDirectory(prefix="maintenance-process-", dir=temporary_root)
        self._cleanup_safe = False
        self.addCleanup(self.cleanup_directory)
        self.root = Path(self.temporary.name)
        for directory in (self.root / "empty-codex-home", self.root / "isolated-temp",
                          self.root / "empty-profile" / "AppData" / "Roaming",
                          self.root / "empty-profile" / "AppData" / "Local"):
            directory.mkdir(parents=True)
        self.path = self.root / "isolated.db"
        shutil.copyfile(fixtures.MaintenanceStateTests.seed_db, self.path)
        self.layout = Layout.from_paths(self.path, self.root / "outputs")
        self.layout.output.mkdir()
        (self.layout.output / "old-image.png").write_bytes(b"old image, not a new generation")
        while True:
            with socket.socket() as reserved:
                reserved.bind(("127.0.0.1", 0))
                self.port = reserved.getsockname()[1]
            if self.port != 8765:
                break
        self.addCleanup(self.close_managed)

    def command(self, action, *options):
        environment = fixture_environment(self.root, self.path)
        return subprocess.run([sys.executable, "-m", "creatoros.maintenance", action,
            "--database-url", "sqlite:///" + self.path.as_posix(),
            "--output-root", str(self.layout.output), "--port", str(self.port), *options],
            cwd=PROJECT_ROOT, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=100)

    def cleanup_directory(self):
        if not self._cleanup_safe:
            # Leave evidence in place if we could not prove the fixture stopped.
            self.temporary._finalizer.detach()
            raise RuntimeError(f"测试实例未确认退出，保留隔离目录：{self.root}")
        self.temporary.cleanup()

    def close_managed(self):
        self._cleanup_safe = False
        if self.layout.instance_file.exists():
            self._cleanup_trace = close_test_instance(self.layout, self.port)
        elif port_open(self.port):
            raise RuntimeError("隔离端口仍被占用且缺少实例登记，保留测试目录。")
        elif self.layout.control_dir.exists():
            wait_closed(self.layout, self.port, 1)
        self._cleanup_safe = True

    def test_routine_and_full_restart_preserve_old_rsi_rows_files_and_idle_queue(self):
        before = snapshot(self.layout)
        check = self.command("check")
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.assertFalse(self.layout.control_dir.exists(), "check must not create control state")
        started = self.command("restart")
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        initial_report = json.loads(started.stdout)
        self.assertEqual(initial_report["verification"], "startup")
        initial_evidence = Path(initial_report["evidence"])
        self.assertEqual(read_json(initial_evidence / "before.json"), {"verification": "startup"})
        with closing(sqlite3.connect(initial_evidence / "before.sqlite")) as backup:
            old_state = backup.execute("SELECT state_json FROM skill_iterations").fetchone()[0]
            self.assertIn("old criterion", old_state)
            self.assertIn('"old_score": 35', old_state)
        first = read_json(self.layout.instance_file)
        status = control(first, "status")
        self.assertFalse(status["held"])
        self.assertTrue(status["workers_started"])
        self.assertTrue(status["idle"], status)
        with urlopen(f"http://127.0.0.1:{self.port}/", timeout=10) as response:
            self.assertEqual(response.status, 200)
        verify_snapshot(self.layout, before)
        restarted = self.command("restart", "--full")
        self.assertEqual(restarted.returncode, 0, restarted.stdout + restarted.stderr)
        self.assertEqual(json.loads(restarted.stdout)["verification"], "full")
        second = read_json(self.layout.instance_file)
        self.assertNotEqual(first["instance_id"], second["instance_id"])
        verify_snapshot(self.layout, before)
        evidence = sorted((self.layout.control_dir / "evidence").glob("*/verified.json"))
        self.assertEqual(len(evidence), 2)
        for path, mode in zip(evidence, ("startup", "full")):
            result = read_json(path)
            self.assertTrue(result["consumers_deferred"])
            self.assertEqual(result["verification"], mode)
            if mode == "full":
                self.assertTrue(result["original_rows_unchanged"])
                self.assertEqual(result["files_unchanged"], len(before["files"]))
            else:
                self.assertNotIn("original_rows_unchanged", result)
                self.assertNotIn("files_unchanged", result)

    def test_cleanup_retries_actual_busy_response_and_closes_same_fixture(self):
        started = self.command("restart")
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        receipt = read_json(self.layout.instance_file)
        view = self.layout.chats / "cleanup-busy-injection/view.json"
        view.parent.mkdir(parents=True)
        actual_control = control
        rejections = []
        def control_with_busy_once(current, action):
            if action == "shutdown" and not rejections:
                view.write_text(json.dumps({"status": "running", "requests": []}), encoding="utf-8")
                try:
                    return actual_control(current, action)
                except ControlError as error:
                    rejections.append(str(error))
                    raise
                finally:
                    view.unlink()
            return actual_control(current, action)
        from unittest.mock import patch
        with patch(__name__ + ".control", side_effect=control_with_busy_once):
            self.close_managed()
        self.assertEqual(rejections, ["受管服务拒绝 shutdown（HTTP 409）；未强制停止进程。"])
        self.assertTrue(self._cleanup_safe)
        self.assertIn({"rejected": "shutdown", "http": 409}, self._cleanup_trace)
        self.assertFalse(self.layout.instance_file.exists())
        self.assertFalse(port_open(self.port))
        self.assertEqual(receipt["port"], self.port)

    def test_pending_migration_automatically_uses_full_checks(self):
        from alembic.script import ScriptDirectory
        from creatoros.storage import upgrade_database
        head = ScriptDirectory(str(PROJECT_ROOT / "migrations")).get_revision("head")
        self.path = self.root / "older.db"
        upgrade_database("sqlite:///" + self.path.as_posix(), head.down_revision)
        self.layout = Layout.from_paths(self.path, self.layout.output)
        before = snapshot(self.layout)
        started = self.command("restart")
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        result = json.loads(started.stdout)
        self.assertEqual(result["verification"], "full")
        self.assertTrue(result["original_rows_unchanged"])
        verify_snapshot(self.layout, before)

    def test_authorized_pending_queue_refuses_restart_without_starting_any_process(self):
        # Explicit queued-work fixture, no executor/producer is started.
        with closing(sqlite3.connect(self.path)) as db, db:
            run, revision, version = db.execute(
                "SELECT r.id,v.id,r.version FROM content_runs r "
                "JOIN content_revisions v ON v.content_run_id=r.id").fetchone()
            db.execute("INSERT INTO production_queue_items "
                "(run_id,revision_id,expected_version,status,submitted_at) VALUES (?,?,?,'pending',CURRENT_TIMESTAMP)",
                (run, revision, version))
        before = snapshot(self.layout)
        refused = self.command("restart")
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn("production_queue_items", refused.stdout)
        self.assertFalse(self.layout.instance_file.exists())
        self.assertFalse((self.layout.control_dir / "evidence").exists())
        verify_snapshot(self.layout, before)

    def test_busy_chat_refuses_restart_without_closing_writes_or_changing_instance(self):
        started = self.command("restart")
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        receipt = read_json(self.layout.instance_file)
        view = self.layout.chats / "fault-injected/view.json"
        view.parent.mkdir(parents=True)
        view.write_text(json.dumps({"status": "running", "requests": []}), encoding="utf-8")
        try:
            before = snapshot(self.layout)
            refused = self.command("restart")
            self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
            status = control(receipt, "status")
            self.assertFalse(status["held"])
            self.assertFalse(status["idle"])
            self.assertEqual(receipt["instance_id"], read_json(self.layout.instance_file)["instance_id"])
            verify_snapshot(self.layout, before)
        finally:
            view.unlink()  # Remove only our non-worker fault fixture before graceful test cleanup.


if __name__ == "__main__":
    unittest.main()
