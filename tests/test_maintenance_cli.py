"""Controlled malformed-receipt and disk-fault injection, never a real host."""
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from creatoros.maintenance import __main__ as cli
from creatoros.maintenance.state import Layout, MaintenanceError


class MaintenanceCliFaultTests(unittest.TestCase):
    def receipt(self, layout):
        import sys
        return dict(instance_id="test", token="t" * 40, port=12345, protocol=1, pid=123,
                    layout=layout.fingerprint(), project_root=str(cli.PROJECT_ROOT.resolve()),
                    executable=str(Path(sys.executable).resolve()))

    def test_malformed_registration_fails_closed_without_a_network_request(self):
        with TemporaryDirectory() as root:
            layout = Layout.from_paths(Path(root) / "fixture.db", Path(root) / "outputs")
            for field, value in (("instance_id", None), ("protocol", 0), ("pid", False), ("token", [])):
                receipt = self.receipt(layout)
                receipt[field] = value
                with patch.object(cli, "read_json", return_value=receipt), \
                     patch("urllib.request.build_opener") as network:
                    with self.assertRaises(MaintenanceError):
                        cli.managed(layout, 12345)
                    network.assert_not_called()

    def test_non_object_http_response_is_a_control_error_not_an_attribute_error(self):
        with TemporaryDirectory() as root:
            layout = Layout.from_paths(Path(root) / "fixture.db", Path(root) / "outputs")
            with patch("urllib.request.build_opener") as network:
                network.return_value.open.return_value = io.BytesIO(b"[]")
                with self.assertRaises(cli.ControlError):
                    cli.control(self.receipt(layout), "status")

    def test_failed_error_log_cannot_skip_release_of_not_stopped_old_host(self):
        with TemporaryDirectory() as root:
            layout = Layout.from_paths(Path(root) / "fixture.db", Path(root) / "outputs")
            layout.control_dir.mkdir()
            receipt, actions = self.receipt(layout), []

            def record(path, value):
                if path.name == "failure.json":
                    raise OSError("simulated full disk for diagnostics")

            with patch.object(cli, "check", return_value={"idle": True}), \
                 patch.object(cli, "port_open", return_value=True), \
                 patch.object(cli, "managed", return_value=(receipt, {})), \
                 patch.object(cli, "control", side_effect=lambda _receipt, action: actions.append(action)), \
                 patch.object(cli, "inspect_idle", return_value={"idle": True}), \
                 patch.object(cli, "write_new", side_effect=record), \
                 patch.object(cli, "snapshot", side_effect=OSError("original snapshot failure")), \
                 patch.object(cli, "launch") as launch, patch("sys.stderr", new_callable=io.StringIO):
                with self.assertRaisesRegex(OSError, "original snapshot failure"):
                    cli._restart(layout, 12345, 1)
                self.assertEqual(actions, ["begin", "release"])
                launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
