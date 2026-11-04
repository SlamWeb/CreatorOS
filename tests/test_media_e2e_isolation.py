"""Private media browser fixtures and their loopback HTTP entrypoint."""
from __future__ import annotations

import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from zipfile import ZipFile

import httpx

from tests.test_isolated_dev import REPO, free_ports, wait_ready


def safe_environment(base):
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("CREATOROS_")
           and not any(secret in key.upper() for secret in
                       ("TOKEN", "SECRET", "PASSWORD", "COOKIE", "CREDENTIAL", "API_KEY"))}
    env.update(DATABASE_URL=f"sqlite:///{(base / 'unrelated.db').as_posix()}",
               PYTHON_DOTENV_DISABLED="1", CODEX_HOME=str(base / "codex-home"),
               TEMP=str(base), TMP=str(base), TMPDIR=str(base))
    return env


class MediaBrowserIsolationTest(unittest.TestCase):
    def test_server_exposes_an_explicit_source_output_root(self):
        with tempfile.TemporaryDirectory(prefix="media-server-help-") as temporary:
            base = Path(temporary)
            result = subprocess.run([sys.executable, "-m", "tests.media_collections_live_server", "--help"],
                                    cwd=REPO, env=safe_environment(base), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout.decode("utf-8", errors="replace"))
            self.assertIn("--output-root", result.stdout.decode("utf-8", errors="replace"))

    def test_server_does_not_default_to_real_database_or_account(self):
        with tempfile.TemporaryDirectory(prefix="media-server-required-") as temporary:
            base = Path(temporary)
            result = subprocess.run([sys.executable, "-m", "tests.media_collections_live_server",
                                     "--root", str(base / "server"), "--dist", str(base / "dist")],
                                    cwd=REPO, env=safe_environment(base), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            for required in ("--source-db", "--output-root", "--creator", "--port"):
                self.assertIn(required, result.stdout.decode("utf-8", errors="replace"))
            self.assertFalse((base / "server").exists())
            self.assertFalse((base / "unrelated.db").exists())

    def test_browser_config_uses_only_explicit_source_output_dist_and_port(self):
        parent = REPO / "tmp"
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="media-config-proof-", dir=parent) as temporary:
            base = Path(temporary)
            source = base / "source.db"
            source.touch()
            outputs = base / "outputs"
            outputs.mkdir()
            dist = base / "dist"
            dist.mkdir()
            (dist / "index.html").write_text("fixture", encoding="utf-8")
            report = base / "report.json"
            env = safe_environment(base)
            env.update(MEDIA_SOURCE_DB=str(source), MEDIA_OUTPUT_ROOT=str(outputs), MEDIA_DIST=str(dist),
                       MEDIA_TEST_ROOT=str(base / "server"), MEDIA_TEST_OUTPUT=str(base / "results"),
                       CREATOROS_E2E_PORT="18993", CREATOROS_PYTHON=sys.executable,
                       PLAYWRIGHT_JSON_OUTPUT_FILE=str(report))
            result = subprocess.run(["node", "node_modules/@playwright/test/cli.js", "test", "--config",
                                     "playwright.media.config.ts", "--list", "--reporter=json"],
                                    cwd=REPO / "web", env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout.decode("utf-8", errors="replace"))
            config = json.loads(report.read_text(encoding="utf-8"))["config"]
            server = config["webServer"]
            self.assertIn(str(source), server["command"])
            self.assertIn(str(outputs), server["command"])
            self.assertIn(str(dist), server["command"])
            self.assertIn("18993", server["command"])
            self.assertEqual(Path(config["projects"][0]["outputDir"]), base / "results")

    def test_browser_config_rejects_missing_or_foreign_sources_and_old_results(self):
        parent = REPO / "tmp"
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="media-config-guards-", dir=parent) as temporary, \
                tempfile.TemporaryDirectory(prefix="media-foreign-", dir=REPO.parent) as foreign:
            base = Path(temporary)
            source = base / "source.db"
            source.touch()
            outputs, dist, existing = base / "outputs", base / "dist", base / "existing"
            for directory in (outputs, dist, existing):
                directory.mkdir()
            (dist / "index.html").write_text("fixture", encoding="utf-8")
            marker = existing / "preserve.txt"
            marker.write_text("unchanged", encoding="utf-8")
            outside = Path(foreign) / "foreign.db"
            outside.touch()
            env = safe_environment(base)
            env.update(MEDIA_SOURCE_DB=str(source), MEDIA_OUTPUT_ROOT=str(outputs), MEDIA_DIST=str(dist),
                       MEDIA_TEST_ROOT=str(base / "server"), MEDIA_TEST_OUTPUT=str(base / "results"),
                       CREATOROS_E2E_PORT="18994", CREATOROS_PYTHON=sys.executable)
            command = ["node", "node_modules/@playwright/test/cli.js", "test", "--config",
                       "playwright.media.config.ts", "--list"]
            cases = [("MEDIA_SOURCE_DB", None), ("MEDIA_SOURCE_DB", str(outside)),
                     ("MEDIA_TEST_OUTPUT", str(existing)), ("CREATOROS_E2E_PORT", "8765")]
            for name, value in cases:
                variant = env.copy()
                if value is None:
                    variant.pop(name)
                else:
                    variant[name] = value
                result = subprocess.run(command, cwd=REPO / "web", env=variant, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90)
                self.assertNotEqual(result.returncode, 0, result.stdout.decode("utf-8", errors="replace"))
                self.assertIn(name, result.stdout.decode("utf-8", errors="replace"))
            self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")
            self.assertFalse((base / "server").exists())

    def test_fresh_fixture_has_two_separate_validated_image_runs(self):
        parent = REPO / "tmp"
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="media-fixture-proof-", dir=parent) as temporary:
            base = Path(temporary)
            root = base / "new-fixture"
            result = subprocess.run([sys.executable, "-m", "tests.media_collections_fixture", "--root", str(root)],
                                    cwd=REPO, env=safe_environment(base), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout.decode("utf-8", errors="replace"))
            fixture = json.loads((root / "fixture.json").read_text(encoding="utf-8"))
            self.assertEqual(fixture["creator_id"], "review-lab")
            self.assertEqual(len(set(fixture["run_ids"])), 2)
            self.assertEqual(Path(fixture["source_db"]), root / "review.db")
            self.assertEqual(Path(fixture["output_root"]), root / "outputs")
            self.assertEqual(fixture["checks"], {"unique_runs": True, "validated_images": True,
                                               "local_producer_calls": True, "workers_stopped": True})
            with closing(sqlite3.connect(fixture["source_db"])) as database:
                rows = database.execute("SELECT id, topic_id, status FROM content_runs ORDER BY id").fetchall()
                validated = database.execute("SELECT artifact_digest, validation_json FROM content_revisions").fetchall()
            self.assertEqual({row[0] for row in rows}, set(fixture["run_ids"]))
            self.assertEqual(len({row[1] for row in rows}), 2)
            self.assertTrue(all(row[2] == "awaiting_approval" for row in rows))
            self.assertEqual(len(validated), 2)
            self.assertTrue(all(digest and json.loads(validation)["images"] for digest, validation in validated))
            self.assertFalse((base / "unrelated.db").exists())

    def test_server_uses_private_fixture_and_real_collection_http_without_mutating_sources(self):
        parent = REPO / "tmp"
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="media-http-proof-", dir=parent) as temporary:
            base = Path(temporary)
            source_root, server_root, dist = base / "source", base / "server", base / "dist"
            dist.mkdir()
            (dist / "index.html").write_text("media isolated build", encoding="utf-8")
            env = safe_environment(base)
            seeded = subprocess.run([sys.executable, "-m", "tests.media_collections_fixture", "--root", str(source_root)],
                                    cwd=REPO, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
            self.assertEqual(seeded.returncode, 0, seeded.stdout.decode("utf-8", errors="replace"))
            setup = json.loads((source_root / "fixture.json").read_text(encoding="utf-8"))
            source_db, output_root = Path(setup["source_db"]), Path(setup["output_root"])
            original_db = hashlib.sha256(source_db.read_bytes()).hexdigest()
            original_images = {path: hashlib.sha256(path.read_bytes()).hexdigest()
                               for path in output_root.rglob("*.png")}
            port, = free_ports(1)
            with (base / "server.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen([sys.executable, "-m", "tests.media_collections_live_server",
                    "--root", str(server_root), "--dist", str(dist), "--source-db", str(source_db),
                    "--output-root", str(output_root), "--creator", setup["creator_id"], "--port", str(port)],
                    cwd=REPO, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
                try:
                    wait_ready(process, f"http://127.0.0.1:{port}/test/media-fixture")
                    with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=15) as client:
                        self.assertEqual(client.get("/").text, "media isolated build")
                        fixture = client.get("/test/media-fixture").json()
                        self.assertEqual(set(fixture["run_ids"]), set(setup["run_ids"]))
                        self.assertFalse(fixture["workers_started"])
                        base_url = f"/api/creators/{setup['creator_id']}/collections"
                        created = client.post(base_url, json={"name": "isolated collection", "request_id": "isolation-test",
                                                              "run_ids": setup["run_ids"]})
                        self.assertEqual(created.status_code, 201, created.text)
                        collection = created.json()
                        self.assertEqual({item["run_id"] for item in collection["items"]}, set(setup["run_ids"]))
                        self.assertEqual(len(collection["items"]), 4)
                        for item in collection["items"]:
                            image = client.get(item["url"])
                            self.assertEqual(image.status_code, 200, image.text)
                            self.assertEqual(hashlib.sha256(image.content).hexdigest(), item["checksum"])
                        download = client.get(f"{base_url}/{collection['id']}/download", params={
                            "version": collection["version"], "snapshot": collection["snapshot_digest"]})
                        self.assertEqual(download.status_code, 200, download.text)
                        with ZipFile(BytesIO(download.content)) as archive:
                            manifest = json.loads(archive.read("manifest.json"))
                            self.assertEqual([item["checksum"] for item in manifest["items"]],
                                             [item["checksum"] for item in collection["items"]])
                        self.assertEqual(client.post("/api/creators", json={"display_name": "must not write"}).status_code, 403)
                        preserved = client.get("/test/media-fixture").json()
                        self.assertTrue(preserved["source_run_rows_unchanged"])
                        self.assertTrue(preserved["source_images_unchanged"])
                    self.assertEqual(hashlib.sha256(source_db.read_bytes()).hexdigest(), original_db)
                    self.assertEqual({path: hashlib.sha256(path.read_bytes()).hexdigest() for path in original_images}, original_images)
                    self.assertFalse((base / "unrelated.db").exists())
                except Exception:
                    log.flush()
                    print((base / "server.log").read_text(encoding="utf-8", errors="replace"))
                    raise
                finally:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=15)

    def test_playwright_worker_can_use_media_config_without_opening_a_browser(self):
        parent = REPO / "tmp"
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="media-worker-proof-", dir=parent) as temporary:
            base = Path(temporary)
            source, dist = base / "source", base / "dist"
            dist.mkdir()
            (dist / "index.html").write_text("fixture", encoding="utf-8")
            env = safe_environment(base)
            seeded = subprocess.run([sys.executable, "-m", "tests.media_collections_fixture", "--root", str(source)],
                                    cwd=REPO, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
            self.assertEqual(seeded.returncode, 0, seeded.stdout.decode("utf-8", errors="replace"))
            port, = free_ports(1)
            env.update(MEDIA_SOURCE_DB=str(source / "review.db"), MEDIA_OUTPUT_ROOT=str(source / "outputs"),
                       MEDIA_DIST=str(dist), MEDIA_TEST_ROOT=str(base / "server"),
                       MEDIA_TEST_OUTPUT=str(base / "results"), MEDIA_CREATOR_ID="review-lab",
                       CREATOROS_E2E_PORT=str(port), CREATOROS_PYTHON=sys.executable)
            result = subprocess.run(["node", "node_modules/@playwright/test/cli.js", "test", "--config",
                                     "playwright.media.config.ts", "media-tests/fixture-http.spec.ts"],
                                    cwd=REPO / "web", env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=240)
            output = result.stdout.decode("utf-8", errors="replace")
            self.assertEqual(result.returncode, 0, output)
            self.assertIn("1 passed", output)
            self.assertTrue((base / "server" / "isolated.sqlite").is_file())
            self.assertFalse((base / "unrelated.db").exists())


if __name__ == "__main__":
    unittest.main()
