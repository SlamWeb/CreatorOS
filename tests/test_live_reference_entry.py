"""Live reference entry must serve an explicit independent build and database."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import httpx

from tests.test_isolated_dev import REPO, free_ports, wait_ready


def isolated_environment(temporary):
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("CREATOROS_")
           and not any(secret in key.upper() for secret in
                       ("TOKEN", "SECRET", "PASSWORD", "COOKIE", "CREDENTIAL", "API_KEY"))}
    env.update(DATABASE_URL=f"sqlite:///{(Path(temporary) / 'unrelated.db').as_posix()}",
               CODEX_HOME=str(Path(temporary) / "codex-home"), PYTHON_DOTENV_DISABLED="1",
               CREATOROS_CODEX_EXECUTABLE=str(Path(temporary) / "no-codex"),
               CREATOROS_POWERSHELL_EXECUTABLE=str(Path(temporary) / "no-powershell"),
               CREATOROS_MEDIA_DOWNLOADER=str(Path(temporary) / "no-downloader"))
    return env


class LiveReferenceEntryTest(unittest.TestCase):
    def test_help_requires_an_explicit_build_directory(self):
        with tempfile.TemporaryDirectory(prefix="creatoros-live-help-") as temporary:
            result = subprocess.run([sys.executable, "-m", "tests.live_topic_references", "--help"],
                                    cwd=REPO, env=isolated_environment(temporary), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout.decode("utf-8", errors="replace"))
            self.assertIn("--dist", result.stdout.decode("utf-8", errors="replace"))
            self.assertFalse((Path(temporary) / "unrelated.db").exists())

    def test_rejects_missing_or_shared_dist_and_existing_root_before_any_server(self):
        parent = REPO / "tmp"
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="live-entry-guard-", dir=parent) as temporary:
            base = Path(temporary)
            root = base / "must-not-exist"
            env = isolated_environment(temporary)
            command = [sys.executable, "-m", "tests.live_topic_references", "--serve", "--root", str(root)]
            for extra in ([], ["--dist", str(REPO / "web")],
                          ["--dist", str(base / "missing-dist")]):
                result = subprocess.run(command + extra, cwd=REPO, env=env, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("--dist", result.stdout.decode("utf-8", errors="replace"))
                self.assertFalse(root.exists())
            dist = base / "dist"
            dist.mkdir()
            (dist / "index.html").write_text("test fixture", encoding="utf-8")
            denied = subprocess.run(command + ["--dist", str(dist), "--port", "8765"], cwd=REPO, env=env,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
            self.assertNotEqual(denied.returncode, 0)
            self.assertIn("8765", denied.stdout.decode("utf-8", errors="replace"))
            self.assertFalse(root.exists())
            root.mkdir()
            marker = root / "preserve-evidence"
            marker.write_text("unchanged", encoding="utf-8")
            denied = subprocess.run(command + ["--dist", str(dist)], cwd=REPO, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
            self.assertNotEqual(denied.returncode, 0)
            self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")
            self.assertFalse((base / "unrelated.db").exists())

    def test_http_serves_selected_build_and_uses_only_new_database(self):
        parent = REPO / "tmp"
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="live-entry-http-", dir=parent) as temporary:
            base = Path(temporary)
            root = base / "new-run"
            dist = base / "independent-dist"
            dist.mkdir()
            content = b"<html><body>independent-live-fixture</body></html>"
            (dist / "index.html").write_bytes(content)
            port, = free_ports(1)
            env = isolated_environment(temporary)
            with (base / "server.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen([sys.executable, "-m", "tests.live_topic_references", "--serve",
                    "--root", str(root), "--dist", str(dist), "--port", str(port)], cwd=REPO, env=env,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
                try:
                    wait_ready(process, f"http://127.0.0.1:{port}/api/health")
                    with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=10) as client:
                        response = client.get("/")
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.content, content)
                    setup = json.loads((root / "setup.json").read_text(encoding="utf-8"))
                    self.assertEqual(Path(setup["studio_dist"]), dist.resolve())
                    self.assertEqual(setup["studio_dist_files"]["index.html"], hashlib.sha256(content).hexdigest())
                    self.assertTrue((root / "studio.db").is_file())
                    self.assertFalse((base / "unrelated.db").exists())
                except Exception:
                    log.flush()
                    print((base / "server.log").read_text(encoding="utf-8", errors="replace"))
                    raise
                finally:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=15)


if __name__ == "__main__":
    unittest.main()
