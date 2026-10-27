"""Real local audio bytes and SQLite, no model/network substitution."""
import base64
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import wave
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from time import sleep
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
