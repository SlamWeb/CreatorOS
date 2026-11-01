"""Real isolated SQLite/ZIP persistence with explicit filesystem fault injection.

ReviewProducer makes local fixture images; these tests do not call Codex or
establish what caused any historical Windows export failure.
"""
import hashlib
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from fastapi.testclient import TestClient

from tests.studio_review_fixtures import make_fixture
from tests.test_media_collections import business_snapshot


class MediaExportRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db, self.runs, self.producer, app = make_fixture(self.root)
        self.service = app.state.media_collections
        self.addCleanup(self.db.close)
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.producer.count = 2
        run = self.runs.create("review-1")
        self.runs.execute(run.id)
        self.base = "/api/creators/review-lab/collections"
        response = self.client.post(self.base, json={
            "name": "ZIP recovery", "request_id": "recovery-fixture", "run_ids": [run.id]})
        self.assertEqual(response.status_code, 201, response.text)
        self.collection = response.json()
        self.base += "/" + self.collection["id"]
        self.before, self.original_files = business_snapshot(self.db), self.files()
        self.producer_calls = self.producer.calls
        self.addCleanup(self.assert_originals_unchanged)

    def assert_originals_unchanged(self):
        self.assertEqual(business_snapshot(self.db), self.before)
        self.assertEqual(self.files(), self.original_files)
        self.assertEqual(self.producer.calls, self.producer_calls)

    def files(self):
        return {str(path.relative_to(self.root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (self.root / "outputs").rglob("*") if path.is_file()}

    def export(self):
        return self.client.post(self.base + "/exports", json={
            "expected_version": self.collection["version"],
            "snapshot_digest": self.collection["snapshot_digest"]})

    @staticmethod
    def fault(winerror=32, errno=13):
        error = OSError(errno, "private C:/not-for-logs/secret-token")
        error.winerror = winerror
        return error

    def assert_zip(self, response):
        self.assertEqual(response.status_code, 201, response.text)
        receipt = response.json()
        downloaded = self.client.get(receipt["url"])
        self.assertEqual(downloaded.status_code, 200, downloaded.text)
        self.assertEqual(hashlib.sha256(downloaded.content).hexdigest(), receipt["checksum"])
        originals = [self.client.get(item["url"]).content for item in self.collection["items"]]
        with ZipFile(BytesIO(downloaded.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["snapshot_digest"], self.collection["snapshot_digest"])
            self.assertEqual([archive.read(f"{i:04d}.png") for i in range(1, 3)], originals)
        return receipt, downloaded.content

    def test_transient_windows_publish_sharing_conflict_recovers_same_request(self):
        original_rename, publication_calls = Path.rename, []

        def share_once(source, target):
            if source.parent.name.endswith(".media-exports"):
                publication_calls.append((source, target))
                if len(publication_calls) <= 2:
                    # Fault injection: the OS sharing condition is deliberate,
                    # while ZIP creation, persistence and HTTP remain real.
                    raise self.fault(winerror=31 + len(publication_calls))
            return original_rename(source, target)

        with patch.object(Path, "rename", share_once), patch(
                "creatoros.web.media_exports.sleep") as delay:
            response = self.export()
        self.assert_zip(response)
        self.assertEqual(len(publication_calls), 3)
        self.assertEqual([call.args[0] for call in delay.call_args_list], [0.01, 0.02])

    def test_persistent_publish_lock_is_bounded_and_logs_only_safe_error_chain(self):
        def locked(*_args):
            raise self.fault(33) from self.fault(112, 28)

        # A second injected cleanup error must not replace the publish failure.
        original_unlink = Path.unlink

        def cleanup_locked(path, *args, **kwargs):
            if path.name == "images.zip" and path.parent.parent == self.service.exports.root:
                raise self.fault()
            return original_unlink(path, *args, **kwargs)

        with self.assertLogs("creatoros.web.media_exports", "WARNING") as logs, patch.object(
                Path, "rename", side_effect=locked) as rename, patch.object(
                Path, "unlink", cleanup_locked), patch("creatoros.web.media_exports.sleep") as delay:
            response = self.export()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "export_unavailable")
        self.assertEqual(rename.call_count, 4)
        self.assertEqual([call.args[0] for call in delay.call_args_list], [0.01, 0.02, 0.04])
        diagnostic = "\n".join(logs.output)
        self.assertIn("stage=publish", diagnostic)
        self.assertIn("stage=cleanup", diagnostic)
        self.assertIn('"winerror": 33', diagnostic)
        self.assertIn('"errno": 28', diagnostic)
        self.assertIn('"winerror": 112', diagnostic)
        for forbidden in ("secret-token", "not-for-logs", str(self.root), "Traceback"):
            self.assertNotIn(forbidden, diagnostic)
        self.assertNotIn("secret-token", response.text)

    def test_nonsharing_publish_errors_fail_without_retry(self):
        for winerror, errno in ((5, 13), (112, 28), (None, 5)):
            with self.subTest(winerror=winerror), patch.object(
                    Path, "rename", side_effect=self.fault(winerror, errno)) as rename, patch(
                    "creatoros.web.media_exports.sleep") as delay:
                response = self.export()
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["error"]["code"], "export_unavailable")
            self.assertEqual(rename.call_count, 1)
            delay.assert_not_called()

    def test_rename_completed_before_error_reads_exact_export_and_replays_identical_bytes(self):
        original_rename = Path.rename

        def published_then_unknown(source, target):
            original_rename(source, target)
            raise self.fault()

        with patch.object(Path, "rename", published_then_unknown), patch.object(
                self.service, "download", wraps=self.service.download) as build, patch(
                "creatoros.web.media_exports.sleep") as delay:
            receipt, original_zip = self.assert_zip(self.export())
            second_receipt, replayed_zip = self.assert_zip(self.export())
            self.assertEqual(build.call_count, 1)
            delay.assert_not_called()
        self.assertEqual(receipt, second_receipt)
        self.assertEqual(original_zip, replayed_zip)
        self.assertEqual(self.client.get(self.base + "/exports/" + receipt["export_id"]).json(), receipt)

    def test_publish_readback_short_lock_retries_validation_without_rebuilding(self):
        original_read = Path.read_text
        read_calls = []

        def read_locked_once(path, *args, **kwargs):
            if path.name == "metadata.json" and path.parent.parent == self.service.exports.root:
                read_calls.append(path)
                if len(read_calls) == 1:
                    raise self.fault()
            return original_read(path, *args, **kwargs)

        with patch.object(Path, "read_text", read_locked_once), patch.object(
                self.service, "download", wraps=self.service.download) as build, patch(
                "creatoros.web.media_exports.sleep") as delay:
            response = self.export()
            self.assertEqual(len(read_calls), 2)
            self.assertEqual(build.call_count, 1)
            self.assertEqual([call.args[0] for call in delay.call_args_list], [0.01])
        self.assert_zip(response)


if __name__ == "__main__":
    unittest.main()
