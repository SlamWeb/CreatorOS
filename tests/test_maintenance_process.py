"""Actual local command -> managed HTTP -> new process, never formal data/models."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from urllib.request import urlopen

from creatoros.config import PROJECT_ROOT
from creatoros.maintenance.__main__ import control, read_json, wait_closed
from creatoros.maintenance.state import Layout, snapshot, verify_snapshot
from tests import test_maintenance_state as fixtures


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
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "isolated.db"
        shutil.copyfile(fixtures.MaintenanceStateTests.seed_db, self.path)
        self.layout = Layout.from_paths(self.path, self.root / "outputs")
        self.layout.output.mkdir()
        (self.layout.output / "old-image.png").write_bytes(b"old image, not a new generation")
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            self.port = reserved.getsockname()[1]
        self.addCleanup(self.close_managed)

    def command(self, action):
        environment = os.environ.copy()
        environment["DEEPSEEK_API_KEY"] = ""  # No model input is authorized by these fixtures.
        return subprocess.run([sys.executable, "-m", "creatoros.maintenance", action,
            "--database-url", "sqlite:///" + self.path.as_posix(),
            "--output-root", str(self.layout.output), "--port", str(self.port)],
            cwd=PROJECT_ROOT, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=100)

    def close_managed(self):
        if self.layout.instance_file.exists():
            receipt = read_json(self.layout.instance_file)
            control(receipt, "begin")
            control(receipt, "shutdown")
            wait_closed(self.layout, self.port, 30, receipt.get("pid"))

    def test_offline_start_and_actual_restart_preserve_old_rsi_rows_files_and_idle_queue(self):
        before = snapshot(self.layout)
        check = self.command("check")
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.assertFalse(self.layout.control_dir.exists(), "check must not create control state")
        started = self.command("restart")
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        first = read_json(self.layout.instance_file)
        status = control(first, "status")
        self.assertFalse(status["held"])
        self.assertTrue(status["workers_started"])
        self.assertTrue(status["idle"], status)
        with urlopen(f"http://127.0.0.1:{self.port}/", timeout=10) as response:
            self.assertEqual(response.status, 200)
        verify_snapshot(self.layout, before)
        restarted = self.command("restart")
        self.assertEqual(restarted.returncode, 0, restarted.stdout + restarted.stderr)
        second = read_json(self.layout.instance_file)
        self.assertNotEqual(first["instance_id"], second["instance_id"])
        verify_snapshot(self.layout, before)
        evidence = sorted((self.layout.control_dir / "evidence").glob("*/verified.json"))
        self.assertEqual(len(evidence), 2)
        for path in evidence:
            result = read_json(path)
            self.assertTrue(result["consumers_deferred"])
            self.assertTrue(result["original_rows_unchanged"])

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
