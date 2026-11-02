"""Exercise two independent development stacks through real HTTP and browsers."""
from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import URLError
from urllib.request import urlopen


REPO = Path(__file__).resolve().parent.parent


def free_ports(count):
    sockets = []
    try:
        while len(sockets) < count:
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            if sock.getsockname()[1] == 8765:
                sock.close()
                continue
            sockets.append(sock)
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


def wait_ready(process, url):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"stack exited before readiness: {process.returncode}")
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, URLError):
            pass
        time.sleep(.25)
    raise AssertionError(f"stack did not become ready: {url}")


class IsolatedDevelopmentTest(unittest.TestCase):
    def test_vite_requires_an_explicit_api_target(self):
        port, = free_ports(1)
        with tempfile.TemporaryDirectory(prefix="creatoros-vite-guard-") as temporary:
            env = os.environ.copy()
            env.pop("CREATOROS_API_URL", None)
            env.update(CREATOROS_VITE_CACHE_DIR=str(Path(temporary) / "vite-cache"),
                       TEMP=temporary, TMP=temporary, TMPDIR=temporary)
            process = subprocess.Popen(["node", "node_modules/vite/bin/vite.js", "--configLoader", "runner",
                                        "--host", "127.0.0.1", "--port", str(port)],
                                       cwd=REPO / "web", env=env, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            try:
                output, _ = process.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.communicate(timeout=10)
                self.fail("Vite started without an explicit API target")
            self.assertNotEqual(process.returncode, 0)
            self.assertIn("CREATOROS_API_URL", output.decode("utf-8", errors="replace"))

    def test_vite_preview_needs_no_api_target(self):
        port, = free_ports(1)
        with tempfile.TemporaryDirectory(prefix="creatoros-preview-proof-") as temporary:
            root = Path(temporary)
            dist = root / "dist"
            dist.mkdir()
            (dist / "index.html").write_text("preview-isolated", encoding="utf-8")
            env = os.environ.copy()
            env.pop("CREATOROS_API_URL", None)
            env.update(CREATOROS_BUILD_OUT_DIR=str(dist), CREATOROS_VITE_CACHE_DIR=str(root / "cache"),
                       TEMP=temporary, TMP=temporary, TMPDIR=temporary)
            process = subprocess.Popen(["node", "node_modules/vite/bin/vite.js", "preview", "--configLoader", "runner",
                                        "--host", "127.0.0.1", "--port", str(port)], cwd=REPO / "web", env=env,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                wait_ready(process, f"http://127.0.0.1:{port}/")
                with urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
                    self.assertEqual(response.read(), b"preview-isolated")
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)

    def test_real_api_startup_and_refusal_of_existing_data_or_production_port(self):
        api, vite = free_ports(2)
        with tempfile.TemporaryDirectory(prefix="creatoros-real-dev-proof-") as temporary:
            base = Path(temporary)
            root = base / "fresh"
            env = os.environ.copy()
            env.update(DEEPSEEK_API_KEY="", OPENAI_API_KEY="", CODEX_API_KEY="")
            args = [sys.executable, "-m", "scripts.isolated_dev", "--root", str(root),
                    "--api-port", str(api), "--vite-port", str(vite), "--stop-on-stdin"]
            with (base / "real.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen(args, cwd=REPO, stdin=subprocess.PIPE, stdout=log,
                                           stderr=subprocess.STDOUT, text=True, env=env)
                try:
                    wait_ready(process, f"http://127.0.0.1:{api}/api/health")
                    wait_ready(process, f"http://127.0.0.1:{vite}/api/health")
                    self.assertTrue((root / "studio.db").is_file())
                except Exception:
                    log.flush()
                    print((base / "real.log").read_text(encoding="utf-8", errors="replace"))
                    raise
                finally:
                    if process.poll() is None:
                        process.stdin.write("stop\n")
                        process.stdin.flush()
                        process.wait(timeout=20)
                    process.stdin.close()
            marker = root / "preserve-me"
            marker.write_text("unchanged", encoding="utf-8")
            rejected = subprocess.run(args, cwd=REPO, env=env, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")
            bad_port = args.copy()
            bad_port[bad_port.index("--api-port") + 1] = "8765"
            bad_port[bad_port.index("--root") + 1] = str(base / "must-not-exist")
            rejected = subprocess.run(bad_port, cwd=REPO, env=env, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertFalse((base / "must-not-exist").exists())

    def test_two_stacks_and_browser_suites_do_not_share_data(self):
        api_a, vite_a, api_b, vite_b = free_ports(4)
        # By default this also works within one checkout. Set this variable to
        # exercise a second, independently installed Git worktree end to end.
        second = Path(os.environ.get("CREATOROS_SECOND_WORKTREE", REPO)).resolve()
        self.assertTrue((second / "scripts" / "isolated_dev.py").is_file())
        self.assertTrue((second / "web" / "node_modules" / "@playwright" / "test" / "cli.js").is_file())
        with tempfile.TemporaryDirectory(prefix="creatoros-isolation-proof-") as temporary:
            base = Path(temporary)
            stacks = []
            browsers = []
            try:
                for label, api, vite, checkout in (("a", api_a, vite_a, REPO), ("b", api_b, vite_b, second)):
                    log = (base / f"{label}.log").open("w", encoding="utf-8")
                    process = subprocess.Popen([
                        sys.executable, "-m", "scripts.isolated_dev", "--root", str(base / label),
                        "--api-port", str(api), "--vite-port", str(vite), "--fixtures", "--stop-on-stdin",
                    ], cwd=checkout, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, text=True)
                    stacks.append((process, log))
                for process, _ in stacks:
                    self.assertIsNone(process.poll())
                for process, api, vite in zip((item[0] for item in stacks), (api_a, api_b), (vite_a, vite_b)):
                    wait_ready(process, f"http://127.0.0.1:{api}/api/health")
                    wait_ready(process, f"http://127.0.0.1:{vite}/api/health")

                for label, own, other, checkout in (("a", vite_a, vite_b, REPO), ("b", vite_b, vite_a, second)):
                    node = checkout / "web" / "node_modules" / "@playwright" / "test" / "cli.js"
                    env = os.environ.copy()
                    env.update(CREATOROS_ISOLATION_LABEL=label,
                               CREATOROS_ISOLATION_URL=f"http://127.0.0.1:{own}",
                               CREATOROS_ISOLATION_OTHER_URL=f"http://127.0.0.1:{other}",
                               CREATOROS_ISOLATION_RESULT=str(base / label / "browser-results"))
                    log = (base / f"browser-{label}.log").open("w", encoding="utf-8")
                    process = subprocess.Popen(["node", str(node), "test", "--config", "playwright.isolation.config.ts"],
                                               cwd=checkout / "web", env=env, stdout=log, stderr=subprocess.STDOUT)
                    browsers.append((process, log))
                for process, _ in browsers:
                    self.assertEqual(process.wait(timeout=120), 0)
                for label in ("a", "b"):
                    root = base / label
                    self.assertTrue((root / "studio.db").is_file())
                    self.assertTrue(any((root / "studio-agent-sessions").rglob("attachments/*/image")))
                    self.assertTrue(any((root / "outputs").rglob("*.png")))
                    self.assertTrue((root / "browser-results").is_dir())
            except Exception:
                for _, log in stacks + browsers:
                    log.flush()
                for path in base.glob("*.log"):
                    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                    print(f"\n--- {path.name} (last 60 lines) ---\n" + "\n".join(lines[-60:]))
                raise
            finally:
                for process, _ in browsers:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=10)
                for process, _ in stacks:
                    if process.poll() is None:
                        process.stdin.write("stop\n")
                        process.stdin.flush()
                        process.wait(timeout=20)
                    process.stdin.close()
                for _, log in stacks + browsers:
                    log.close()


if __name__ == "__main__":
    unittest.main()
