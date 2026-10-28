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

from creatoros.storage import ContentRepository, Database, upgrade_database


class MaterialTests(unittest.TestCase):
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
                analysis_receipt = {"inputSha256": receipt["sha256"], "sourceStartSeconds": 0,
                                    "sourceEndSeconds": 1}
                onsets = {"analysisOffset": 0, "analysisDurationSeconds": 1,
                          "events": [{"id": "event-1", "timeSeconds": 0.5, "accepted": True}]}
                store.save_analysis("a", receipt["id"], {"mood": "soft"}, analysis_receipt, onsets)
                self.assertTrue(store.list("a")[0]["analysis_available"])
                self.assertTrue(store.resolve_analysis("a", receipt["id"])["path"].is_file())
                self.assertEqual(store.start_analysis("a", receipt["id"])["status"], "completed")
                status_path = store.resolve("a", receipt["id"])["path"].parent / "bgm-analysis-status.json"
                status_path.write_text('{"status":"running"}', encoding="utf-8")
                self.assertEqual(MaterialStore(db).analysis_status("a", receipt["id"])["status"], "interrupted")
                with patch.dict(os.environ, {"CREATOROS_BGM_PYTHON": str(Path(temporary) / "missing-python")}):
                    self.assertEqual(store.start_analysis("a", receipt["id"], force=True)["status"], "running")
                    deadline = monotonic() + 5
                    while store.analysis_status("a", receipt["id"])["status"] == "running" and monotonic() < deadline:
                        sleep(0.02)
                    self.assertEqual(store.analysis_status("a", receipt["id"])["status"], "failed")
                self.assertTrue(store.resolve_analysis("a", receipt["id"])["path"].is_file())
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
