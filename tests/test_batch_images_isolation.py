"""Local HTTP/config/admission proofs; no browser launch or model call."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import base64
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
import socket
import subprocess
import sys
import tempfile
import unittest

import httpx
from PIL import Image

from tests.live_batch_images_server import BatchGate
from tests.test_isolated_dev import REPO, free_ports, wait_ready


class BatchImagesIsolationTests(unittest.TestCase):
    def setUp(self):
        (REPO / "tmp").mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="batch-isolation-proof-", dir=REPO / "tmp")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root, self.dist = self.base / "server", self.base / "dist"
        self.dist.mkdir()
        (self.dist / "index.html").write_text("private batch build", encoding="utf-8")
        self.port, = free_ports(1)
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("CREATOROS_BATCH_IMAGES")}
        self.env.update(DEEPSEEK_API_KEY="", OPENAI_API_KEY="", CODEX_API_KEY="", PYTHON_DOTENV_DISABLED="1")

    def command(self, *extra):
        return [sys.executable, "-m", "tests.live_batch_images_server", "--root", str(self.root),
                "--dist", str(self.dist), "--port", str(self.port), *extra]

    def run_command(self, command, env=None, web=False):
        return subprocess.run(command, cwd=REPO / "web" if web else REPO, env=env or self.env,
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90)

    def test_root_admission_is_atomic_and_keeps_unknown_outcome_without_replay(self):
        self.root.mkdir()
        gate = BatchGate(self.root, "fixture", True)
        reservation = gate.reserve()
        with self.assertRaises(FileExistsError):
            gate.reserve()
        payload = {"request_id": "first-paid-request", "upload_ids": [hashlib.sha256(str(i).encode()).hexdigest() for i in range(15)]}
        with self.assertRaises(PermissionError):
            gate.claim(payload, "foreign-token")
        with self.assertRaises(ValueError):
            gate.claim({**payload, "upload_ids": payload["upload_ids"][:14]}, reservation["token"])
        self.assertFalse((self.root / "batch-submission.json").exists())

        def claim():
            try:
                gate.claim(payload, reservation["token"])
                return "admitted"
            except FileExistsError:
                return "denied"
        with ThreadPoolExecutor(2) as workers:
            self.assertEqual(sorted(workers.map(lambda _: claim(), range(2))), ["admitted", "denied"])
        receipt = (self.root / "batch-submission.json").read_bytes()
        saved = json.loads(receipt)
        self.assertEqual(saved["job_id"], hashlib.sha256(payload["request_id"].encode()).hexdigest())
        self.assertIn("unknown", saved["state"])
        with self.assertRaises(FileExistsError):
            BatchGate(self.root, "fixture", True).claim({**payload, "request_id": "different"}, reservation["token"])
        self.assertEqual((self.root / "batch-submission.json").read_bytes(), receipt)
        with self.assertRaises(PermissionError):
            BatchGate(self.root, "fixture", False).claim(payload, reservation["token"])

    def test_server_refuses_production_targets_old_roots_and_implicit_live_auth(self):
        old = self.base / "old"
        old.mkdir()
        marker = old / "preserve.txt"
        marker.write_text("unchanged", encoding="utf-8")
        for name, value in (("--root", str(old)), ("--root", str(REPO / "data" / "must-not-create")),
                            ("--dist", str(REPO / "web" / "dist")), ("--port", "8765"), ("--port", "0")):
            command = self.command()
            command[command.index(name) + 1] = value
            self.assertNotEqual(self.run_command(command).returncode, 0)
            self.assertFalse(self.root.exists())
        denied = self.run_command(self.command("--live-models"))
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn(b"credentials", denied.stdout)
        self.assertFalse(self.root.exists())
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", self.port))
            listener.listen()
            self.assertNotEqual(self.run_command(self.command()).returncode, 0)
            self.assertFalse(self.root.exists())
        self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")

    def test_real_prepare_http_accepts_15_local_uploads_but_never_dispatches(self):
        self.env.update(CODEX_HOME=str(self.base / "foreign-codex"), DEEPSEEK_API_KEY="must-not-be-used",
                        DATABASE_URL=f"sqlite:///{(self.base / 'foreign.db').as_posix()}")
        with (self.base / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(self.command(), cwd=REPO, env=self.env, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT)
            try:
                url = f"http://127.0.0.1:{self.port}"
                wait_ready(process, url + "/test/batch/fixture")
                with httpx.Client(base_url=url, trust_env=False, timeout=15) as client:
                    fixture = client.get("/test/batch/fixture").json()
                    self.assertFalse(fixture["live_models"])
                    self.assertFalse(fixture["background_services_started"])
                    self.assertEqual(client.get("/").text, "private batch build")
                    self.assertEqual(client.post("/api/skill-extractions/uploads", json={}).status_code, 403)
                    reserved = client.post("/test/batch/reserve", json={})
                    self.assertEqual(reserved.status_code, 200)
                    self.assertEqual(client.post("/test/batch/reserve", json={}).status_code, 409)
                    headers = {"x-creatoros-batch-token": reserved.json()["token"]}
                    ids = []
                    for number in range(15):
                        image = BytesIO()
                        Image.new("RGB", (12, 16), (number * 13, 40, 80)).save(image, format="PNG")
                        data = image.getvalue()
                        uploaded = client.post("/api/skill-extractions/uploads", headers=headers,
                            json={"name": f"sample-{number}.png", "data_base64": base64.b64encode(data).decode()})
                        self.assertEqual(uploaded.status_code, 201, uploaded.text)
                        ids.append(uploaded.json()["id"])
                        self.assertEqual(ids[-1], hashlib.sha256(data).hexdigest())
                        self.assertEqual(client.get(uploaded.json()["url"]).content, data)
                    self.assertEqual(len(set(ids)), 15)
                    denied = client.post("/api/skill-extractions", headers=headers,
                                         json={"request_id": "no-paid-dispatch", "upload_ids": ids})
                    self.assertEqual(denied.status_code, 403)
                    self.assertEqual(client.get("/api/skill-extractions").json()["items"], [])
                    for endpoint in ("/api/runs", "/api/agent/sessions", "/api/skill-extractions/job/save",
                                     "/api/skill-extractions/job/trials", "/api/skill-extractions/job/revise"):
                        self.assertEqual(client.post(endpoint, json={}).status_code, 403)
                env = self.config_environment(url, "0", self.base / "api-worker-results")
                checked = self.run_command(self.playwright_command(), env=env, web=True)
                self.assertEqual(checked.returncode, 0, checked.stdout.decode(errors="replace"))
                self.assertIn(b"1 passed", checked.stdout)
                self.assertFalse((self.root / "batch-submission.json").exists())
                with closing(sqlite3.connect(self.root / "studio.db")) as database:
                    self.assertEqual(database.execute("SELECT count(*) FROM content_runs").fetchone()[0], 0)
                self.assertFalse((self.base / "foreign.db").exists())
                self.assertFalse((self.base / "foreign-codex").exists())
            except Exception:
                log.flush()
                print((self.base / "server.log").read_text(encoding="utf-8", errors="replace"))
                raise
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=15)

    def playwright_command(self, *extra):
        return ["node", "node_modules/@playwright/test/cli.js", "test", "--config",
                "live-images/playwright.batch.config.ts", *extra]

    def config_environment(self, url, mode, output):
        return {**self.env, "CREATOROS_BATCH_IMAGES_ROOT": str(self.root), "CREATOROS_BATCH_IMAGES_URL": url,
                "CREATOROS_BATCH_IMAGES_LIVE": mode, "CREATOROS_BATCH_IMAGES_OUTPUT": str(output)}

    def test_config_only_lists_paid_spec_and_refuses_foreign_or_reused_outputs(self):
        self.root.mkdir()
        for name in ("temp", "codex-home", "skills"):
            (self.root / name).mkdir()
        url = f"http://127.0.0.1:{self.port}"
        setup = {"root": str(self.root), "dist": str(self.dist), "temp": str(self.root / "temp"),
                 "codex_home": str(self.root / "codex-home"), "database": str(self.root / "studio.db"),
                 "skills_root": str(self.root / "skills"), "kind": "batch-images-15-v1",
                 "fixture_id": "synthetic-config-only", "live_models": True, "url": url}
        (self.root / "batch-setup.json").write_text(json.dumps(setup), encoding="utf-8")
        output = self.base / "fresh-results"
        env = self.config_environment(url, "1", output)
        report = self.base / "listing.json"
        env["PLAYWRIGHT_JSON_OUTPUT_FILE"] = str(report)
        command = self.playwright_command("--list", "--reporter=json")
        checked = self.run_command(command, env=env, web=True)
        self.assertEqual(checked.returncode, 0, checked.stdout.decode(errors="replace"))
        listing = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(Path(listing["config"]["projects"][0]["outputDir"]), output)
        self.assertIn("extraction-batch.live.spec.ts", json.dumps(listing["suites"]))
        self.assertFalse((self.root / "batch-reservation.json").exists())
        self.assertFalse(output.exists())
        old = self.base / "old-results"
        old.mkdir()
        marker = old / "preserve.txt"
        marker.write_text("unchanged", encoding="utf-8")
        for key, value in (("CREATOROS_BATCH_IMAGES_LIVE", None), ("CREATOROS_BATCH_IMAGES_URL", "http://127.0.0.1:8765"),
                           ("CREATOROS_BATCH_IMAGES_ROOT", str(REPO / "data")),
                           ("CREATOROS_BATCH_IMAGES_OUTPUT", str(old)), ("CREATOROS_BATCH_IMAGES_OUTPUT", str(self.root / "nested"))):
            variant = env.copy()
            if value is None:
                variant.pop(key)
            else:
                variant[key] = value
            self.assertNotEqual(self.run_command(command, env=variant, web=True).returncode, 0)
        self.assertNotEqual(self.run_command(command, env=env, web=True).returncode, 0)
        self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")
        legacy = ["node", "node_modules/@playwright/test/cli.js", "test", "--config", "live-images/playwright.config.ts", "--list"]
        blocked = self.run_command(legacy, env={**self.env, "CREATOROS_BATCH_IMAGES": "1"}, web=True)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn(b"playwright.batch.config.ts", blocked.stdout)
        (self.root / "setup.json").write_text(json.dumps({"creator_id": "local-chat-config-only",
            "series_id": "local-series", "skill_id": "local-skill"}), encoding="utf-8")
        compatible = self.run_command(legacy, env={**self.env, "CREATOROS_IMAGES_ROOT": str(self.root)}, web=True)
        self.assertEqual(compatible.returncode, 0, compatible.stdout.decode(errors="replace"))
        self.assertIn(b"chat-images.live.spec.ts", compatible.stdout)


if __name__ == "__main__":
    unittest.main()
