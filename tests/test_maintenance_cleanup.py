"""Fast, isolated maintenance cleanup protocol fault injection."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from creatoros.maintenance.__main__ import ControlError
from tests import test_maintenance_process as process_fixture


class MaintenanceCleanupTests(unittest.TestCase):
    def setUp(self):
        self.layout = SimpleNamespace(instance_file="unused")
        self.receipt = {"instance_id": "fixture", "port": 41001, "pid": 123}

    def status(self, *, held, idle, identity="fixture", pid=123):
        return {"instance_id": identity, "pid": pid, "held": held, "idle": idle,
                "info": {"busy": [] if idle else ["worker_state_locked"]}}

    def test_child_environment_discards_inherited_settings_and_credentials(self):
        inherited = {"PATH": "fixture-path", "creatoros_worker_count": "host-config",
                     "XHS_TOKEN": "sensitive", "SOME_SECRET_FILE": "sensitive",
                     "DB_PASSWORD": "sensitive", "BROWSER_COOKIE": "sensitive",
                     "SERVICE_CREDENTIAL": "sensitive", "MIXED_API_KEY_SETTING": "sensitive",
                     "CODEX_HOME": "host-codex", "DATABASE_URL": "host-db"}
        root = Path("fixture-root")
        env = process_fixture.fixture_environment(root, root / "isolated.db", source=inherited)
        for name in ("creatoros_worker_count", "XHS_TOKEN", "SOME_SECRET_FILE", "DB_PASSWORD",
                     "BROWSER_COOKIE", "SERVICE_CREDENTIAL", "MIXED_API_KEY_SETTING"):
            self.assertNotIn(name, env)
        self.assertEqual(env["PATH"], "fixture-path")
        self.assertEqual(env["CODEX_HOME"], str(root / "empty-codex-home"))
        self.assertEqual(env["CREATOROS_XHS_MCP_TOKEN_FILE"], str(root / "nonexistent-platform-token"))
        self.assertEqual(env["CREATOROS_XHS_MCP_URL"], "http://127.0.0.1:1/mcp")
        self.assertEqual(env["PYTHON_DOTENV_DISABLED"], "1")
        self.assertIn("isolated.db", env["DATABASE_URL"])

    def test_transient_busy_shutdown_rechecks_before_retry(self):
        sequence = [self.status(held=False, idle=True), self.status(held=True, idle=True),
                    self.status(held=True, idle=False), self.status(held=True, idle=True)]
        actions = []
        def managed(_layout, _port):
            actions.append("status")
            return self.receipt, sequence.pop(0)
        def control(_receipt, action):
            actions.append(action)
            if action == "shutdown" and actions.count("shutdown") == 1:
                raise ControlError("受管服务拒绝 shutdown（HTTP 409）；未强制停止进程。")
            return {"instance_id": "fixture"}
        with patch.object(process_fixture, "read_json", return_value=self.receipt), \
             patch.object(process_fixture, "managed", side_effect=managed), \
             patch.object(process_fixture, "control", side_effect=control), \
             patch.object(process_fixture, "wait_closed") as closed, \
             patch.object(process_fixture.time, "sleep"):
            trace = process_fixture.close_test_instance(self.layout, 41001)
        self.assertEqual(actions, ["status", "begin", "status", "shutdown", "status", "status", "shutdown"])
        self.assertEqual(trace[2], {"rejected": "shutdown", "http": 409})
        closed.assert_called_once_with(self.layout, 41001, 30, 123)

    def test_persistently_busy_never_requests_shutdown(self):
        busy = self.status(held=True, idle=False)
        with patch.object(process_fixture, "read_json", return_value=self.receipt), \
             patch.object(process_fixture, "managed", return_value=(self.receipt, busy)), \
             patch.object(process_fixture, "control") as control, \
             patch.object(process_fixture.time, "monotonic", side_effect=[0, 0, 2]), \
             patch.object(process_fixture.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "未强制终止"):
                process_fixture.close_test_instance(self.layout, 41001, timeout=1)
        control.assert_not_called()

    def test_changed_instance_or_port_never_receives_mutation(self):
        for receipt, status in (({**self.receipt, "instance_id": "other"}, self.status(held=True, idle=True)),
                                ({**self.receipt, "port": 41002}, self.status(held=True, idle=True)),
                                (self.receipt, self.status(held=True, idle=True, pid=124)),
                                (self.receipt, self.status(held=True, idle=True, identity="other"))):
            with self.subTest(receipt=receipt, status=status):
                with patch.object(process_fixture, "read_json", return_value=self.receipt), \
                     patch.object(process_fixture, "managed", return_value=(receipt, status)), \
                     patch.object(process_fixture, "control") as control:
                    with self.assertRaisesRegex(RuntimeError, "身份或端口"):
                        process_fixture.close_test_instance(self.layout, 41001)
                control.assert_not_called()

    def test_instance_change_after_begin_blocks_shutdown(self):
        statuses = [(self.receipt, self.status(held=False, idle=True)),
                    ({**self.receipt, "instance_id": "replacement"}, self.status(held=True, idle=True))]
        with patch.object(process_fixture, "read_json", return_value=self.receipt), \
             patch.object(process_fixture, "managed", side_effect=statuses), \
             patch.object(process_fixture, "control") as control:
            with self.assertRaisesRegex(RuntimeError, "身份或端口"):
                process_fixture.close_test_instance(self.layout, 41001)
        control.assert_called_once_with(self.receipt, "begin")

    def test_unknown_shutdown_preserves_fixture_directory(self):
        temporary = Mock()
        fixture = SimpleNamespace(_cleanup_safe=False, temporary=temporary, root="isolated-root")
        with self.assertRaisesRegex(RuntimeError, "保留隔离目录"):
            process_fixture.MaintenanceProcessTests.cleanup_directory(fixture)
        temporary._finalizer.detach.assert_called_once()
        temporary.cleanup.assert_not_called()

    def test_non_409_error_is_not_retried(self):
        with patch.object(process_fixture, "read_json", return_value=self.receipt), \
             patch.object(process_fixture, "managed", return_value=(self.receipt, self.status(held=True, idle=True))), \
             patch.object(process_fixture, "control", side_effect=ControlError("connection unknown")) as control:
            with self.assertRaisesRegex(ControlError, "connection unknown"):
                process_fixture.close_test_instance(self.layout, 41001)
        self.assertEqual(control.call_count, 1)
