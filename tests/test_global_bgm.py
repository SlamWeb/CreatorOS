"""Isolated project DB and file system tests with controlled analyzer and share source."""
import base64
from contextlib import ExitStack
import hashlib
import io
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave
from uuid import uuid4

from creatoros.integrations.bgm_share import public_connection, share_url
from creatoros.integrations.global_bgm import GlobalBgmStore, LEGACY_SCHEMA
from creatoros.integrations.managed_materials import MaterialStore
from creatoros.integrations.media_tools import media_tool, probe_media
from creatoros.integrations.whole_bgm_analysis import SCHEMA
from creatoros.storage import ContentRepository, Database, TopicSource, upgrade_database
from creatoros.storage.models import GlobalBgmTrack, ProductionTask


METADATA = {"has_audio": True, "has_video": False, "duration_seconds": 1.0}


def wave_bytes(sample=0):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as sink:
        sink.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        sink.writeframes(sample.to_bytes(2, "little") * 8000)
    return stream.getvalue()


def analysis(audio, _target):
    return json.dumps({"schema": SCHEMA, "source_sha256": audio["sha256"]}).encode(), {
        "schema": SCHEMA, "analyzer_version": "controlled", "summary": {"beat_count": 2}}


class GlobalBgmTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        url = f"sqlite:///{Path(temporary.name).as_posix()}/pool.db"
        upgrade_database(url)
        self.db = Database(url)
        self.addCleanup(self.db.close)
        self.repo = ContentRepository(self.db)
        for owner in ("a", "b"):
            self.repo.create_creator(creator_id=owner, display_name=owner)
            self.repo.create_series(series_id=owner, name=f"Series {owner}", description="", audience="",
                                    skill_name=None, creator_id=owner)
        self.legacy = MaterialStore(self.db)
        self.store = GlobalBgmStore(self.db, legacy=self.legacy)
        patches = ExitStack()
        self.addCleanup(patches.close)
        for name in ("global_bgm", "managed_materials"):
            module = "creatoros.integrations." + name
            patches.enter_context(patch(module + ".probe_media", return_value=METADATA))
            patches.enter_context(patch(module + ".decode_media"))
        self.analyze = patches.enter_context(patch("creatoros.integrations.global_bgm.run_whole_analysis", side_effect=analysis))

    def until(self, get, status):
        deadline = monotonic() + 8
        while monotonic() < deadline:
            value = get()
            if value["status"] == status:
                return value
            sleep(.025)
        self.fail(f"Expected {status}, found {value}")

    def test_shared_dedup_playlists_archive_and_immutability(self):
        audio = wave_bytes()
        track = self.store.put("Shared.wav", audio)
        identifier = track["id"]
        self.assertEqual(identifier, "bgm-" + hashlib.sha256(audio).hexdigest())
        self.until(lambda: self.store.analysis_status(identifier), "completed")
        pinned_analysis = self.store.resolve_analysis(identifier)
        self.assertEqual(self.store.put("Copy.wav", audio)["id"], identifier)
        self.assertEqual(self.analyze.call_count, 1)
        self.assertEqual(self.store.list("sHaReD")[0]["name"], "Shared.wav")
        for owner in ("a", "b", "b"):
            self.store.add_to_series(owner, identifier)
        self.assertEqual(self.store.usage(identifier)["reference_count"], 2)
        self.assertTrue(self.store.archive(identifier)["archived"])
        self.assertEqual(self.store.list(), [])
        self.assertEqual(self.store.list(archived=True)[0]["id"], identifier)
        self.assertEqual(self.store.resolve(identifier)["path"].read_bytes(), audio)
        self.assertEqual(self.store.list_series("a")[0]["id"], identifier)
        with self.assertRaisesRegex(ValueError, "归档"):
            self.store.add_to_series("a", identifier)
        self.assertEqual(self.store.remove_from_series("a", identifier), {"removed": True})
        self.assertEqual(self.store.remove_from_series("a", identifier), {"removed": False})
        self.assertEqual(self.store.list_series("b")[0]["id"], identifier)
        self.store.restore(identifier)
        self.analyze.side_effect = ValueError("Controlled repass failure")
        self.store.start_analysis(identifier, force=True)
        result = self.until(lambda: self.store.analysis_status(identifier), "failed")
        self.assertTrue(result["analysis_available"])
        self.assertEqual(self.store.resolve_analysis(identifier), pinned_analysis)

    def test_stale_queue_stays_interrupted_until_explicit_retry(self):
        identifier = self.store.put("Stale.wav", wave_bytes())["id"]
        self.until(lambda: self.store.analysis_status(identifier), "completed")
        with self.db.session() as session:
            track = session.get(GlobalBgmTrack, identifier)
            track.analysis_status, track.analysis_json = "queued", None
        replacement = GlobalBgmStore(self.db, legacy=self.legacy)
        entered, release = Event(), Event()

        def slow(audio, target):
            entered.set()
            if not release.wait(8):
                raise ValueError("test timeout")
            return analysis(audio, target)

        self.analyze.side_effect = slow
        try:
            ready = replacement.put("Current.wav", wave_bytes(1))["id"]
            self.assertTrue(entered.wait(5))
            self.assertEqual(replacement.analysis_status(identifier)["status"], "interrupted")
            release.set()
            self.until(lambda: replacement.analysis_status(ready), "completed")
            self.assertEqual(replacement.analysis_status(identifier)["status"], "interrupted")
            replacement.start_analysis(identifier)
            self.until(lambda: replacement.analysis_status(identifier), "completed")
        finally:
            release.set()

    def test_legacy_same_bytes_admitted_once_originals_left_intact(self):
        full = wave_bytes()
        item = self.legacy.put("a", "old.wav", full)
        partial = self.legacy.put("b", "partial.wav", wave_bytes(1))
        self.legacy.put("b", "duplicate.wav", full)
        for record, owner, end in ((item, "a", 1.0), (partial, "b", .5)):
            self.legacy.save_analysis(owner, record["id"], {"test": 1}, {
                "sourceStartSeconds": 0, "sourceEndSeconds": end, "inputSha256": record["sha256"]},
                {"analysisDurationSeconds": end, "events": [{"timeSeconds": .2}]})
        pinned = self.legacy.resolve_analysis("b", partial["id"])
        result = {track["name"]: track for track in self.store.list()}
        self.assertEqual(set(result), {"old.wav", "partial.wav"})
        self.assertEqual(self.store.resolve_analysis(result["old.wav"]["id"])["schema"], LEGACY_SCHEMA)
        self.assertIsNone(self.store.resolve_analysis(result["partial.wav"]["id"]))
        self.assertEqual(self.legacy.resolve_analysis("b", partial["id"]), pinned)
        self.assertEqual(self.legacy.resolve("a", item["id"])["path"].read_bytes(), full)
        self.assertEqual(self.analyze.call_count, 0)
        self.repo.add_topic(topic_id="a-topic", series_id="a", title="Topic", source=TopicSource.MANUAL)
        with self.db.session() as session:
            for index, inputs in enumerate(({"bgm_material_id": item["id"]}, {"bgm_track_id": result["old.wav"]["id"]})):
                session.add(ProductionTask(id=f"task-{index}", topic_id="a-topic", title=f"Video {index}",
                    position=index, create_request_id=f"request-{index}", kind="video", definition_json={"inputs": inputs}))
        usage = self.store.usage(result["old.wav"]["id"])
        self.assertEqual(usage["reference_count"], 2)
        self.assertEqual([ref["task_id"] for ref in usage["references"]], ["task-0", "task-1"])

    def test_import_failure_retry_and_repeat(self):
        url = "https://v.douyin.com/example/"
        with patch("creatoros.integrations.global_bgm.download_share", side_effect=[ValueError("Public site refused"),
                                                                                     ("music.wav", wave_bytes())]) as pull:
            identifier = self.store.start_import("分享内容 " + url)["id"]
            self.assertEqual(self.until(lambda: self.store.import_status(identifier), "failed")["message"], "Public site refused")
            self.assertEqual(self.store.start_import("另一个标题 " + url)["status"], "failed")
            self.store.retry_import(identifier)
            completed = self.until(lambda: self.store.import_status(identifier), "completed")
            self.assertIsNotNone(completed["track_id"])
            self.assertEqual(self.store.get(completed["track_id"])["source_type"], "share")
            self.until(lambda: self.store.analysis_status(completed["track_id"]), "completed")
            self.assertEqual(self.store.retry_import(identifier)["track_id"], completed["track_id"])
            self.assertEqual(pull.call_count, 2)

    def test_share_scope_and_resolved_address_guard(self):
        self.assertEqual(share_url("复制 https://b23.tv/fake。"), "https://b23.tv/fake")
        for unsafe in ("https://127.0.0.1/file", "https://b23.tv@192.168.1.1", "https://b23.tv:1234/item"):
            with self.assertRaises(ValueError):
                share_url(unsafe)
        with patch("creatoros.integrations.bgm_share.socket.getaddrinfo", return_value=[
                (None, None, None, "", ("8.8.8.8", 443)), (None, None, None, "", ("127.0.0.1", 443))]), \
                patch("creatoros.integrations.bgm_share.socket.socket") as dial:
            with self.assertRaisesRegex(ValueError, "内网"):
                public_connection("host.example")
            dial.assert_not_called()

    def test_real_video_becomes_audio_only_when_ffmpeg_is_available(self):
        from creatoros.integrations.media_tools import decode_media

        try:
            ffmpeg = media_tool("ffmpeg")
            media_tool("ffprobe")
        except ValueError as error:
            self.skipTest(str(error))
        with TemporaryDirectory() as directory:
            source = Path(directory) / "clip.mp4"
            subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=size=32x32:rate=12",
                            "-f", "lavfi", "-i", "sine=frequency=440", "-t", "1", "-c:v", "mpeg4",
                            "-c:a", "aac", str(source)], capture_output=True, check=True, timeout=20)
            raw = source.read_bytes()
        with patch("creatoros.integrations.global_bgm.probe_media", side_effect=probe_media), \
                patch("creatoros.integrations.global_bgm.decode_media", side_effect=decode_media):
            track = self.store.put("clip.mp4", raw)
        stored = self.store.resolve(track["id"])
        self.until(lambda: self.store.analysis_status(track["id"]), "completed")
        self.assertEqual(stored["kind"], "audio")
        self.assertEqual(stored["path"].suffix, ".m4a")
        self.assertTrue(probe_media(stored["path"])["has_audio"])
        self.assertFalse(probe_media(stored["path"])["has_video"])
        self.assertEqual(stored["source_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertNotEqual(stored["sha256"], stored["source_sha256"])
        self.assertEqual(self.store.put("copy.mp4", raw)["id"], track["id"])

    def test_archived_creator_keeps_legacy_audio_and_import_receipt(self):
        from threading import Event

        from fastapi.testclient import TestClient
        from creatoros.runs import ContentRunService
        from creatoros.web.app import create_app

        root = Path(self.db.engine.url.database).parent
        app = create_app(database=self.db, run_service=ContentRunService(self.db, output_root=root / "outputs"),
                         chat_root=root / "sessions", defer_background_start=True)
        legacy = app.state.materials
        raw = wave_bytes()
        audio = legacy.put("a", "legacy.wav", raw)
        release = Event()

        def controlled_download(*_args):
            if not release.wait(5):
                raise RuntimeError("test timed out")
            raise ValueError("受控下载失败")

        with TestClient(app) as client, patch.object(legacy, "from_url", side_effect=controlled_download):
            imports = "/api/creators/a/materials/imports"
            started = client.post(imports, json={"source_url": "https://www.bilibili.com/video/demo"})
            self.assertEqual(started.status_code, 202, started.text)
            identifier = started.json()["id"]
            try:
                removal = client.get("/api/creators/a/removal").json()
                self.assertEqual(removal["status"], "blocked")
                self.assertIn({"code": "material_jobs", "count": 1}, removal["reasons"])
            finally:
                release.set()
            self.until(lambda: legacy.import_status("a", identifier), "failed")
            archived = client.post("/api/creators/a/delete", json={"expected_status": "archived"})
            self.assertEqual(archived.status_code, 200, archived.text)
            self.assertEqual(client.get(audio["url"]).content, raw)
            self.assertEqual(client.get(f"{imports}/{identifier}").json()["status"], "failed")
            rerun = client.post(f"/api/creators/a/materials/{audio['id']}/analysis/run", json={})
            self.assertEqual(rerun.status_code, 422)

    def test_http_preview_and_playlist_preserve_series_through_removal(self):
        from fastapi.testclient import TestClient
        from creatoros.runs import ContentRunService
        from creatoros.web.app import create_app

        root = Path(self.db.engine.url.database).parent
        runs = ContentRunService(self.db, output_root=root / "outputs")
        app = create_app(database=self.db, run_service=runs, chat_root=root / "sessions", defer_background_start=True)
        self.store = app.state.global_bgm
        audio = wave_bytes()
        with TestClient(app) as client:
            uploaded = client.post("/api/bgm", json={"name": "pool.wav", "data_base64": base64.b64encode(audio).decode()})
            self.assertEqual(uploaded.status_code, 201, uploaded.text)
            identifier = uploaded.json()["id"]
            path = f"/api/bgm/{identifier}"
            result = client.get(path + "/file", headers={"Range": "bytes=0-15"})
            self.assertEqual((result.status_code, result.content), (206, audio[:16]))
            playlist = "/api/series/a/bgm"
            self.assertEqual(client.put(f"{playlist}/{identifier}", json={}).status_code, 200)
            self.assertEqual(client.get("/api/series/a/removal").json()["status"], "archived")
            removed = client.request("DELETE", "/api/series/a", json={
                "expected_revision": 1, "expected_status": "archived", "request_id": uuid4().hex})
            self.assertEqual(removed.status_code, 200, removed.text)
            self.assertEqual(removed.json()["status"], "archived")
            self.assertEqual(client.get(playlist).json()["items"][0]["id"], identifier)
            self.assertEqual(client.post("/api/series/a/restore", json={}).status_code, 200)
            self.assertEqual(client.get(playlist).json()["items"][0]["id"], identifier)
            self.until(lambda: self.store.analysis_status(identifier), "completed")
            self.legacy.put("a", "late legacy.wav", wave_bytes(1))
            app.state.maintenance = SimpleNamespace(held=True)
            self.assertEqual(len(client.get("/api/bgm").json()["items"]), 1)
            app.state.maintenance.held = False
            self.assertEqual(len(client.get("/api/bgm").json()["items"]), 2)
