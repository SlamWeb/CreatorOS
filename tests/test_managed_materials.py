"""Real local audio bytes and SQLite, no model/network substitution."""
import base64
import io
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import wave
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep
from contextlib import ExitStack
from threading import Event

from creatoros.storage import ContentRepository, Database, upgrade_database


class MaterialTests(unittest.TestCase):
    def test_platform_403_explains_failure_without_exposing_downloader_output(self):
        from types import SimpleNamespace
        from creatoros.integrations.managed_materials import MaterialStore
        store = MaterialStore(SimpleNamespace(engine=SimpleNamespace(url=SimpleNamespace(database=":memory:"))))
        dns = [(None, None, None, None, ("8.8.8.8", 443))]
        result = SimpleNamespace(returncode=1, stderr=b"HTTP Error 403: private-debug-value")
        with patch("creatoros.integrations.managed_materials.socket.getaddrinfo", return_value=dns), \
             patch("creatoros.integrations.managed_materials.shutil.which", return_value="downloader"), \
             patch("creatoros.integrations.managed_materials.subprocess.run", return_value=result):
            with self.assertRaisesRegex(ValueError, "HTTP 403") as caught:
                store.from_url("creator", "https://v.douyin.com/example/")
            self.assertNotIn("private-debug-value", str(caught.exception))

    def test_share_import_reuses_running_and_completed_job_and_survives_restart(self):
        from creatoros.integrations.managed_materials import MaterialStore
        with TemporaryDirectory() as temporary:
            url = f"sqlite:///{Path(temporary).as_posix()}/test.db"
            upgrade_database(url)
            db = Database(url)
            try:
                repo = ContentRepository(db)
                for name in ("a", "b"):
                    repo.create_creator(creator_id=name, display_name=name)
                store = MaterialStore(db)
                entered, release = Event(), Event()
                text = "请分析 https://v.douyin.com/one/ 的背景音乐"

                def import_once(creator, source):
                    entered.set()
                    if not release.wait(5):
                        raise RuntimeError("fixture timed out")
                    return {"id": "material-1"}

                with patch.object(store, "from_url", side_effect=import_once) as download, \
                        patch.object(store, "start_analysis", return_value={"status": "running"}) as analyze:
                    first = store.start_import("a", text)
                    self.assertTrue(entered.wait(5))
                    self.assertEqual(store.start_import("a", text)["id"], first["id"])
                    self.assertEqual(download.call_count, 1)
                    release.set()
                    deadline = monotonic() + 5
                    while store.import_status("a", first["id"])["status"] == "running" and monotonic() < deadline:
                        sleep(.02)
                    self.assertEqual(store.import_status("a", first["id"])["status"], "completed")
                    self.assertEqual(store.start_import("a", text)["material_id"], "material-1")
                    self.assertEqual(download.call_count, 1)
                    analyze.assert_called_once_with("a", "material-1")
                self.assertEqual(MaterialStore(db).import_status("a", first["id"])["status"], "completed")
                with self.assertRaises(ValueError):
                    store.import_status("b", first["id"])

                interrupted = store._import_path("a", "a" * 64)
                interrupted.write_text('{"status":"running"}', encoding="utf-8")
                self.assertEqual(MaterialStore(db).import_status("a", "a" * 64)["status"], "interrupted")
                self.assertIn('"running"', interrupted.read_text(encoding="utf-8"))
            finally:
                db.close()

    def test_share_text_limit_matches_http_and_store(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from creatoros.integrations.managed_materials import MaterialStore, MAX_SHARE_TEXT
        from creatoros.web.material_routes import material_routes
        with TemporaryDirectory() as temporary:
            url = f"sqlite:///{Path(temporary).as_posix()}/test.db"
            upgrade_database(url)
            db = Database(url)
            try:
                ContentRepository(db).create_creator(creator_id="a", display_name="A")
                store = MaterialStore(db)
                app = FastAPI()
                app.include_router(material_routes(store))
                prefix = "https://v.douyin.com/one/ "
                text = prefix + "字" * (MAX_SHARE_TEXT - len(prefix))
                with patch.object(store, "from_url", side_effect=ValueError("download blocked for test")), \
                        TestClient(app) as client:
                    response = client.post("/api/creators/a/materials/imports", json={"source_url": text})
                    self.assertEqual(response.status_code, 202)
                    self.assertEqual(response.json()["source_url"], text)
                    job_id = response.json()["id"]
                    deadline = monotonic() + 5
                    while store.import_status("a", job_id)["status"] == "running" and monotonic() < deadline:
                        sleep(.02)
                    self.assertEqual(store.import_status("a", job_id)["status"], "failed")
                    response = client.post("/api/creators/a/materials/imports", json={"source_url": "x" * (MAX_SHARE_TEXT + 1)})
                    self.assertEqual(response.status_code, 422)
                with self.assertRaises(ValueError):
                    store.start_import("a", "x" * (MAX_SHARE_TEXT + 1))
            finally:
                db.close()

    def test_share_import_preserves_failure_and_scopes_status(self):
        from creatoros.integrations.managed_materials import MaterialStore
        with TemporaryDirectory() as temporary:
            url = f"sqlite:///{Path(temporary).as_posix()}/test.db"
            upgrade_database(url)
            db = Database(url)
            try:
                repo = ContentRepository(db)
                for name in ("a", "b"):
                    repo.create_creator(creator_id=name, display_name=name)
                store = MaterialStore(db)
                text = "复制打开抖音 https://v.douyin.com/failed/ 作品"
                with patch.object(store, "from_url", side_effect=ValueError("获取失败")):
                    job = store.start_import("a", text)
                    deadline = monotonic() + 5
                    while store.import_status("a", job["id"])["status"] == "running" and monotonic() < deadline:
                        sleep(.02)
                status = MaterialStore(db).import_status("a", job["id"])
                self.assertEqual(status["status"], "failed")
                self.assertEqual(status["source_url"], text)
                self.assertEqual(status["message"], "获取失败")
                self.assertEqual(len(store.list_imports("a")), 1)
                self.assertEqual(store.list_imports("b"), [])
                with self.assertRaises(ValueError):
                    store.import_status("b", job["id"])
                from fastapi import FastAPI
                from fastapi.testclient import TestClient
                from creatoros.web.material_routes import material_routes
                app = FastAPI()
                app.include_router(material_routes(store))
                with TestClient(app) as client:
                    listing = client.get("/api/creators/a/materials/imports")
                    self.assertEqual(listing.status_code, 200)
                    self.assertEqual(listing.json()["items"][0]["status"], "failed")
                    detail = client.get(f"/api/creators/a/materials/imports/{job['id']}")
                    self.assertEqual(detail.status_code, 200)
                    self.assertEqual(detail.json()["source_url"], text)
            finally:
                db.close()

    def test_share_link_allowlist_rejects_other_origins_before_network_or_downloader(self):
        from creatoros.integrations.managed_materials import MaterialStore
        from types import SimpleNamespace
        store = MaterialStore(SimpleNamespace(engine=SimpleNamespace(url=SimpleNamespace(database=":memory:"))))
        with patch("creatoros.integrations.managed_materials.socket.getaddrinfo") as dns:
            for address in ("https://example.com/music.mp3", "https://youtube.com.evil.test/music", "http://127.0.0.1/music"):
                with self.assertRaisesRegex(ValueError, "其他来源请上传音频"):
                    store.from_url("creator", address)
            dns.assert_not_called()
        with patch("creatoros.integrations.managed_materials.socket.getaddrinfo", return_value=[
                (None, None, None, None, ("127.0.0.1", 80))]):
            with self.assertRaisesRegex(ValueError, "不能指向本机或内网"):
                store.from_url("creator", "https://v.douyin.com/share")
        with patch("creatoros.integrations.managed_materials.socket.getaddrinfo", side_effect=OSError("injected DNS failure")):
            with self.assertRaisesRegex(ValueError, "无法解析分享链接地址"):
                store.from_url("creator", "https://v.douyin.com/share")

    def test_audio_upload_is_immutable_scoped_and_reusable(self):
        from tests.media_test_support import require_media
        require_media(self)
        from creatoros.integrations.managed_materials import MaterialStore
        with TemporaryDirectory() as temporary:
            url = f"sqlite:///{Path(temporary).as_posix()}/test.db"
            upgrade_database(url)
            db = Database(url)
            try:
                repository = ContentRepository(db)
                for name in ("a", "b"):
                    repository.create_creator(creator_id=name, display_name=name)
                output = io.BytesIO()
                with wave.open(output, "wb") as sound:
                    sound.setnchannels(1); sound.setsampwidth(2); sound.setframerate(8000)
                    sound.writeframes(b"\x00\x00" * 8000)
                raw = output.getvalue()
                store = MaterialStore(db)
                receipt = store.upload("a", "music.wav", base64.b64encode(raw).decode())
                self.assertEqual(receipt["kind"], "audio")
                self.assertEqual(receipt["duration_seconds"], 1.0)
                self.assertEqual(store.upload("a", "renamed.wav", base64.b64encode(raw).decode())["id"], receipt["id"])
                self.assertEqual(store.resolve("a", receipt["id"])["path"].read_bytes(), raw)
                with self.assertRaises(ValueError):
                    store.resolve("b", receipt["id"])
                self.assertFalse(store.list("a")[0]["analysis_available"])
                # Listing is used for chat context; do not hash/read each audio.
                with patch.object(store, "resolve", side_effect=AssertionError("unexpected material resolve")), \
                        patch.object(Path, "read_bytes", side_effect=AssertionError("unexpected audio read")):
                    item = store.list("a")[0]
                self.assertEqual(item["analysis_status"], "not_started")
                analysis_receipt = {"inputSha256": receipt["sha256"], "sourceStartSeconds": 0,
                                    "sourceEndSeconds": 1}
                onsets = {"analysisOffset": 0, "analysisDurationSeconds": 1,
                          "events": [{"id": "event-1", "timeSeconds": 0.5, "accepted": True}]}
                store.save_analysis("a", receipt["id"], {"mood": "soft"}, analysis_receipt, onsets)
                self.assertTrue(store.list("a")[0]["analysis_available"])
                saved_analysis = store.resolve_analysis("a", receipt["id"])["path"]
                original = saved_analysis.read_bytes()
                with patch("creatoros.integrations.managed_materials.threading.Thread.start") as launch:
                    self.assertEqual(store.start_analysis("a", receipt["id"], start=0, duration=1)["status"], "completed")
                    launch.assert_not_called()
                status_path = store.resolve("a", receipt["id"])["path"].parent / "bgm-analysis-status.json"
                status_path.write_text('{"status":"running"}', encoding="utf-8")
                self.assertEqual(MaterialStore(db).analysis_status("a", receipt["id"])["status"], "interrupted")
                with patch.dict(os.environ, {"CREATOROS_BGM_PYTHON": str(Path(temporary) / "missing-python")}):
                    self.assertEqual(store.start_analysis("a", receipt["id"], start=.2, duration=.5)["status"], "running")
                    deadline = monotonic() + 5
                    while store.analysis_status("a", receipt["id"])["status"] == "running" and monotonic() < deadline:
                        sleep(0.02)
                    self.assertEqual(store.analysis_status("a", receipt["id"])["status"], "failed")
                self.assertEqual(saved_analysis.read_bytes(), original)
                with patch("creatoros.integrations.managed_materials.threading.Thread.start", side_effect=RuntimeError("cannot start worker")):
                    rejected = store.start_analysis("a", receipt["id"], start=.3, duration=.4)
                self.assertEqual(rejected["status"], "failed")
                self.assertEqual(MaterialStore(db).analysis_status("a", receipt["id"])["status"], "failed")
                self.assertEqual(saved_analysis.read_bytes(), original)
                with patch("creatoros.integrations.managed_materials.threading.Thread.start") as launch:
                    reused = store.start_analysis("a", receipt["id"], start=0, duration=1)
                    self.assertEqual(reused["status"], "completed")
                    launch.assert_not_called()
                self.assertEqual(store.analysis_status("a", receipt["id"])["status"], "completed")
                self.assertEqual(MaterialStore(db).analysis_status("a", receipt["id"])["status"], "completed")
                self.assertEqual(saved_analysis.read_bytes(), original)
                from fastapi import FastAPI
                from fastapi.testclient import TestClient
                from creatoros.web.material_routes import material_routes
                app = FastAPI()
                app.include_router(material_routes(store))
                with TestClient(app) as client:
                    response = client.get(f"/api/creators/a/materials/{receipt['id']}/analysis")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["analysis"]["mood"], "soft")
                    self.assertTrue(client.get("/api/creators/a/materials").json()["items"][0]["analysis_available"])
                with self.assertRaises(ValueError):
                    store.resolve_analysis("b", receipt["id"])
                with self.assertRaises(ValueError):
                    store.save_analysis("a", receipt["id"], {}, {**analysis_receipt, "inputSha256": "0" * 64}, onsets)
                with self.assertRaises(ValueError):
                    store.upload("a", "fake.mp3", base64.b64encode(b"not audio").decode())
                self.assertEqual(len(store.list("a")), 1)
            finally:
                db.close()

    def test_running_analysis_only_reuses_the_same_nonforced_request(self):
        from tests.media_test_support import require_media
        require_media(self)
        from creatoros.integrations.managed_materials import MaterialStore
        with TemporaryDirectory() as temporary:
            url = f"sqlite:///{Path(temporary).as_posix()}/test.db"
            upgrade_database(url)
            db = Database(url)
            try:
                ContentRepository(db).create_creator(creator_id="a", display_name="A")
                output = io.BytesIO()
                with wave.open(output, "wb") as sound:
                    sound.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                    sound.writeframes(b"\x00\x00" * 8000)
                store = MaterialStore(db)
                material = store.put("a", "audio.wav", output.getvalue())
                with patch("creatoros.integrations.managed_materials.threading.Thread.start") as launch:
                    self.assertEqual(store.start_analysis("a", material["id"], start=0, duration=.5)["status"], "running")
                    self.assertEqual(store.start_analysis("a", material["id"], start=0, duration=.5)["status"], "running")
                    with self.assertRaisesRegex(ValueError, "进行中"):
                        store.start_analysis("a", material["id"], start=.2, duration=.5)
                    with self.assertRaisesRegex(ValueError, "进行中"):
                        store.start_analysis("a", material["id"], start=0, duration=.5, force=True)
                    self.assertEqual(launch.call_count, 1)
            finally:
                db.close()

    def test_concurrent_identical_uploads_return_one_immutable_material(self):
        from tests.media_test_support import require_media
        require_media(self)
        from creatoros.integrations.managed_materials import MaterialStore
        with TemporaryDirectory() as temporary, ExitStack() as cleanup:
            url = f"sqlite:///{Path(temporary).as_posix()}/concurrent.db"
            upgrade_database(url)
            db = Database(url)
            cleanup.callback(db.close)
            ContentRepository(db).create_creator(creator_id="creator", display_name="Creator")
            audio = io.BytesIO()
            with wave.open(audio, "wb") as sound:
                sound.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                sound.writeframes(b"\0\0" * 1600)
            raw = audio.getvalue()
            first, second = MaterialStore(db), MaterialStore(db)
            def delay_publication(store):
                original = store._path
                def delayed(row):
                    sleep(0.1)  # widen the file-publication window, without replacing SQLite or audio
                    return original(row)
                return delayed
            with patch.object(first, "_path", delay_publication(first)), patch.object(second, "_path", delay_publication(second)):
                with ThreadPoolExecutor(max_workers=2) as workers:
                    jobs = [workers.submit(store.put, "creator", "music.wav", raw) for store in (first, second)]
                    receipts = [job.result() for job in jobs]
            self.assertEqual(receipts[0]["id"], receipts[1]["id"])
            self.assertEqual(len(first.list("creator")), 1)
            self.assertEqual(first.resolve("creator", receipts[0]["id"])["path"].read_bytes(), raw)
