"""Completed SDK replies deliver bounded drafts without shell/file tool access."""
import base64
import asyncio
import io
import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from threading import Event
from unittest.mock import patch

from PIL import Image
import httpx

from creatoros.integrations.producer_skills import ProducerSkillCatalog
from creatoros.integrations.skill_extraction import SkillExtractionService
from creatoros.integrations.skill_draft_delivery import apply_delivery, existing_context
from creatoros.integrations.skill_draft_files import MODE_FOLDERS


def document(name):
    return f"---\nname: {name}\ndescription: A reusable method with local resources.\n---\nUse this method for a new topic.\n"


def manifest(folder="skill"):
    return {"schema_version": 1, "note": "Model authored content; host writes the draft.",
            "suggested_topic": "Try another color label", "skills": [{"folder": folder,
            "files": [{"path": "SKILL.md", "text": document("color-labels")},
                      {"path": "scripts/render.py", "text": "raise AssertionError('Never execute during extraction')\n"},
                      {"path": "references/layout.md", "text": "# Layout\nKeep the label readable.\n"}],
            "delete_paths": [], "reference_assets": []}]}


class ControlledSdk:
    def __init__(self, reply):
        self.reply, self.calls, self.turn_count = reply, {}, 0
        self.status = "completed"

    @asynccontextmanager
    async def client(self, *_args, **_kwargs):
        owner = self
        class Thread:
            id = "controlled-host-delivery"
            async def turn(self, inputs, **controls):
                owner.turn_count += 1
                owner.calls.update(inputs=inputs, turn=controls)
                return object()
        class Client:
            async def thread_start(self, **controls):
                owner.calls["thread"] = controls
                return Thread()
        yield Client()

    async def collect(self, *_args, **_kwargs):
        return SimpleNamespace(status=self.status, final_response=json.dumps(self.reply), usage=None)


class ExtractionHostDeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        guard = patch("openai_codex.AsyncCodex", side_effect=AssertionError("No real SDK client in isolated tests"))
        guard.start()
        self.addCleanup(guard.stop)
        self.service = SkillExtractionService(ProducerSkillCatalog(self.root / "catalog"))
        self.addCleanup(self.service.shutdown)

    def upload(self, index):
        stream = io.BytesIO()
        Image.new("RGB", (12, 12), (index, 30, 90)).save(stream, format="PNG")
        data = stream.getvalue()
        upload = self.service.upload(f"color-{index}.png", base64.b64encode(data).decode())
        return upload["id"], data

    @unittest.skipUnless(os.name == "nt", "Windows directory sharing semantics")
    def test_sdk_working_directory_is_released_before_host_promotes_draft(self):
        sdk = ControlledSdk(manifest())
        process = None
        @asynccontextmanager
        async def client(*args, **kwargs):
            nonlocal process
            async with sdk.client(*args, **kwargs) as wrapped:
                original_start = wrapped.thread_start
                async def thread_start(**controls):
                    nonlocal process
                    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                               cwd=controls["cwd"], stdout=subprocess.DEVNULL,
                                               stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
                    return await original_start(**controls)
                wrapped.thread_start = thread_start
                try:
                    yield wrapped
                finally:
                    if process:
                        process.terminate()
                        process.wait(timeout=10)
        with patch("creatoros.integrations.skill_extraction._production_client", client), \
             patch("creatoros.integrations.skill_extraction.collect_observed_turn", sdk.collect):
            job = self.service.submit("windows-cwd-lifetime", source_text="An existing reference")
            self.service.worker.join(20)
        current = self.service.get(job["id"])
        self.assertEqual(current["status"], "ready", current.get("error"))
        self.assertIn("Use this method", self.service.file(job["id"], "legacy_end_to_end", "SKILL.md").read_text())

    def test_complete_reply_delivers_fifteen_originals_and_text_assets_without_tools(self):
        images = [self.upload(index) for index in range(15)]
        sdk = ControlledSdk(manifest())
        with patch("creatoros.integrations.skill_extraction._production_client", sdk.client), \
             patch("creatoros.integrations.skill_extraction.collect_observed_turn", sdk.collect):
            job = self.service.submit("fresh-fifteen", [item[0] for item in images], "single", "Keep all originals")
            self.service.worker.join(15)
        current = self.service.get(job["id"])
        self.assertEqual(current["status"], "ready", current.get("error"))
        for index, (_identifier, data) in enumerate(images, 1):
            self.assertEqual(self.service.file(job["id"], "legacy_end_to_end", f"assets/reference-{index:02d}.png").read_bytes(), data)
        self.assertIn("Never execute", self.service.file(job["id"], "legacy_end_to_end", "scripts/render.py").read_text())
        self.assertEqual(len(sdk.calls["inputs"]), 16)
        from openai_codex import ApprovalMode, Sandbox
        self.assertEqual(sdk.calls["thread"]["sandbox"], Sandbox.read_only)
        self.assertEqual(sdk.calls["turn"]["sandbox"], Sandbox.read_only)
        self.assertEqual(sdk.calls["thread"]["approval_mode"], ApprovalMode.deny_all)
        self.assertEqual(sdk.calls["turn"]["approval_mode"], ApprovalMode.deny_all)
        self.assertIn("output_schema", sdk.calls["turn"])
        self.assertEqual([item["id"] for item in self.service.catalog.list()], [self.service.catalog.BUILTIN_ID])
        from creatoros.integrations.producer_skills import _digest
        previous = self.service._draft_root(current)
        previous_digest = _digest(previous)
        sdk.reply = manifest()
        sdk.reply["skills"][0]["files"] = [{"path": "SKILL.md", "text": document("color-labels") + "\nRevise only prose.\n"}]
        with patch("creatoros.integrations.skill_extraction._production_client", sdk.client), \
             patch("creatoros.integrations.skill_extraction.collect_observed_turn", sdk.collect):
            self.service.revise(job["id"], "fifteen-prose-only", current["digest"], "Change only prose; no new pictures")
            self.service.worker.join(15)
        revised = self.service.get(job["id"])
        self.assertEqual(revised["revision"], 2, revised.get("error"))
        self.assertEqual(_digest(previous), previous_digest)
        for index, (_identifier, data) in enumerate(images, 1):
            self.assertEqual(self.service.file(job["id"], "legacy_end_to_end", f"assets/reference-{index:02d}.png").read_bytes(), data)

    def test_all_modes_publish_expected_roles_and_keep_relative_text_resources(self):
        for mode, folders in MODE_FOLDERS.items():
            with self.subTest(mode=mode):
                reply = manifest(folders[0])
                if len(folders) == 2:
                    extra = manifest(folders[1])["skills"][0]
                    extra["files"][0]["text"] = document("visual-labels")
                    reply["skills"].append(extra)
                sdk = ControlledSdk(reply)
                with patch("creatoros.integrations.skill_extraction._production_client", sdk.client), \
                     patch("creatoros.integrations.skill_extraction.collect_observed_turn", sdk.collect):
                    job = self.service.submit("mode-" + mode, mode=mode, source_text="A complete reusable writing sample")
                    self.service.worker.join(15)
                current = self.service.get(job["id"])
                self.assertEqual(current["status"], "ready", current.get("error"))
                for skill in current["skills"]:
                    self.assertIn("Keep the label", self.service.file(job["id"], skill["role"], "references/layout.md").read_text())

    def test_revision_preserves_unmentioned_script_binary_and_old_version(self):
        sdk = ControlledSdk(manifest())
        with patch("creatoros.integrations.skill_extraction._production_client", sdk.client), \
             patch("creatoros.integrations.skill_extraction.collect_observed_turn", sdk.collect):
            job = self.service.submit("revision-seed", source_text="Initial method")
            self.service.worker.join(15)
            original = self.service.get(job["id"])
            old_root = self.service._draft_root(original)
            # A native v1 candidate can already contain binary resources.
            (old_root / "legacy_end_to_end/assets").mkdir(exist_ok=True)
            (old_root / "legacy_end_to_end/assets/shape.bin").write_bytes(b"\x00\xffexisting binary")
            from creatoros.integrations.producer_skills import _digest
            self.service._update(job["id"], digest=_digest(old_root))
            original = self.service.get(job["id"])
            old_digest = _digest(old_root)
            sdk.reply = manifest()
            sdk.reply["skills"][0]["files"] = [{"path": "SKILL.md", "text": document("color-labels") + "\nRevised method.\n"}]
            self.service.revise(job["id"], "revision-one", original["digest"], "Improve only the prose")
            self.service.worker.join(15)
        revised = self.service.get(job["id"])
        self.assertEqual(revised["revision"], 2, revised.get("error"))
        self.assertEqual(_digest(old_root), old_digest)
        self.assertEqual(self.service.file(job["id"], "legacy_end_to_end", "assets/shape.bin").read_bytes(), b"\x00\xffexisting binary")
        self.assertIn("Never execute", self.service.file(job["id"], "legacy_end_to_end", "scripts/render.py").read_text())
        self.assertIn("Never execute", sdk.calls["inputs"][0].text)
        self.assertIn('"preserved_binary": true', sdk.calls["inputs"][0].text)

    def operation(self, name):
        directory = self.root / name
        (directory / "draft").mkdir(parents=True)
        return directory

    def test_sdk_close_error_or_cancel_does_not_publish_and_preserves_existing_draft(self):
        from creatoros.integrations.skill_extraction import sdk_extract
        from creatoros.integrations.producer_skills import _digest
        for failure in ("close_error", "cancel"):
            with self.subTest(failure=failure):
                directory = self.operation("close-" + failure)
                skill = directory / "draft/skill"
                skill.mkdir()
                (skill / "SKILL.md").write_text(document("existing"), encoding="utf-8")
                (skill / "asset.bin").write_bytes(b"existing bytes")
                before = _digest(directory / "draft")
                cancel = Event()
                sdk = ControlledSdk(manifest())
                @asynccontextmanager
                async def client(*args, **kwargs):
                    async with sdk.client(*args, **kwargs) as wrapped:
                        yield wrapped
                        if failure == "close_error":
                            raise OSError("SDK close failed")
                        cancel.set()
                with patch("creatoros.integrations.skill_extraction._production_client", client), \
                     patch("creatoros.integrations.skill_extraction.collect_observed_turn", sdk.collect), \
                     self.assertRaises((OSError, ValueError)):
                    asyncio.run(sdk_extract(directory, [], "single", "update", cancel, lambda _: None))
                self.assertEqual(_digest(directory / "draft"), before)
                self.assertFalse((directory / "draft_delivery.json").exists())
                self.assertEqual(json.loads((directory / "response.txt").read_text(encoding="utf-8")), sdk.reply)

    def test_invalid_paths_and_conflicts_never_write_partial_draft(self):
        for index, path in enumerate(("../outside.md", "C:\\outside.md", "/outside.md", "x/../../outside.md",
                "a\\..\\outside.md", "file.txt:ads", "NUL.txt", "CONIN$.txt", "COM¹.txt", "a./file.md", "assets/../file.md", "x/SKILL.md")):
            reply = manifest()
            reply["skills"][0]["files"].append({"path": path, "text": "forbidden"})
            directory = self.operation(f"invalid-{index}")
            with self.subTest(path=path), self.assertRaises(ValueError):
                apply_delivery(directory, "single", json.dumps(reply), [], "text", Event())
            self.assertEqual(list((directory / "draft").iterdir()), [])
            self.assertFalse((self.root / "outside.md").exists())
        reply = manifest()
        reply["skills"][0]["files"].append({"path": "scripts/RENDER.py", "text": "duplicate"})
        with self.assertRaises(ValueError):
            apply_delivery(self.operation("case-conflict"), "single", json.dumps(reply), [], "text", Event())

    def test_reference_copy_cannot_read_arbitrary_files_or_overwrite_originals(self):
        image_id, data = self.upload(70)
        image = self.service.image_path(image_id)
        for index, asset in enumerate(({"image_index": 2, "path": "assets/missing.png"},
                {"image_index": 1, "path": "../outside.png"}, {"image_index": 1, "path": "references/not-an-asset.png"})):
            reply = manifest("mind")
            reply["skills"][0]["reference_assets"] = [asset]
            with self.assertRaises(ValueError):
                apply_delivery(self.operation(f"bad-ref-{index}"), "mind", json.dumps(reply), [image], "text", Event())
        reply = manifest("mind")
        reply["skills"][0]["reference_assets"] = [{"image_index": 1, "path": "assets/chosen.png"}]
        result = apply_delivery(self.operation("mind-ref"), "mind", json.dumps(reply), [image], "text", Event())
        self.assertEqual((result._directories["mind"] / "assets/chosen.png").read_bytes(), data)
        reply = manifest()
        reply["skills"][0]["files"].append({"path": "Assets/" + image.name, "text": "replace image"})
        with self.assertRaises(ValueError):
            apply_delivery(self.operation("protected-ref"), "single", json.dumps(reply), [image], "text", Event())

    def test_prose_missing_slots_oversize_and_cancel_are_not_success(self):
        from creatoros.integrations import skill_draft_delivery as delivery
        replies = ["SKILL.md was not created because tools rejected the write.", json.dumps(manifest("mind"))]
        for index, reply in enumerate(replies):
            with self.assertRaises(ValueError):
                apply_delivery(self.operation(f"invalid-reply-{index}"), "single", reply, [], "text", Event())
        with patch.object(delivery, "MAX_RESPONSE_BYTES", 20), self.assertRaises(ValueError):
            apply_delivery(self.operation("oversize"), "single", json.dumps(manifest()), [], "text", Event())
        cancelled = Event()
        cancelled.set()
        directory = self.operation("cancelled")
        with self.assertRaises(ValueError):
            apply_delivery(directory, "single", json.dumps(manifest()), [], "text", cancelled)
        self.assertEqual(list((directory / "draft").iterdir()), [])

    def test_linked_existing_resources_are_rejected_before_model_or_host_write(self):
        directory = self.operation("linked")
        source = directory / "draft/skill"
        source.mkdir()
        (source / "SKILL.md").write_text(document("linked-input"), encoding="utf-8")
        outside = self.root / "outside.md"
        outside.write_text("must remain untouched", encoding="utf-8")
        try:
            (source / "linked.md").symlink_to(outside)
        except OSError:
            self.skipTest("Windows does not allow this test account to create symlinks")
        with self.assertRaises(ValueError):
            existing_context(directory, "single")
        with self.assertRaises(ValueError):
            apply_delivery(directory, "single", json.dumps(manifest()), [], "text", Event())
        self.assertEqual(outside.read_text(), "must remain untouched")

    def test_failed_revision_keeps_current_version_and_never_retries_model(self):
        sdk = ControlledSdk(manifest())
        with patch("creatoros.integrations.skill_extraction._production_client", sdk.client), \
             patch("creatoros.integrations.skill_extraction.collect_observed_turn", sdk.collect):
            job = self.service.submit("failed-revision-seed", source_text="Example")
            self.service.worker.join(15)
            initial = self.service.get(job["id"])
            original = self.service.file(job["id"], "legacy_end_to_end", "SKILL.md").read_bytes()
            sdk.reply = "Cannot create SKILL.md; the tool rejected writing."
            self.service.revise(job["id"], "bad-manifest", initial["digest"], "Revise prose")
            self.service.worker.join(15)
        current = self.service.get(job["id"])
        self.assertEqual(current["revision"], 1)
        self.assertEqual(current["digest"], initial["digest"])
        self.assertIn("改稿未完成", current["error"])
        self.assertEqual(self.service.file(job["id"], "legacy_end_to_end", "SKILL.md").read_bytes(), original)
        self.assertEqual(sdk.turn_count, 2)

    def test_real_http_upload_submit_refresh_does_not_repeat_or_register(self):
        from creatoros.storage import Database, upgrade_database
        from creatoros.runs import ContentRunService
        from creatoros.web.app import create_app
        from creatoros.integrations.producer_skills import SkillInstallService
        from tests.agent_studio_support import serve
        url = "sqlite:///" + (self.root / "http.db").as_posix()
        upgrade_database(url)
        database = Database(url)
        self.addCleanup(database.close)
        app = create_app(database=database, skill_extraction_service=self.service,
            skill_install_service=SkillInstallService(self.service.catalog),
            run_service=ContentRunService(database, output_root=self.root / "outputs"),
            chat_root=self.root / "chats", eval_root=self.root / "eval", defer_background_start=True)
        sdk = ControlledSdk(manifest())
        with patch("creatoros.integrations.skill_extraction._production_client", sdk.client), \
             patch("creatoros.integrations.skill_extraction.collect_observed_turn", sdk.collect), \
             serve(app) as base, httpx.Client(base_url=base, trust_env=False, timeout=15) as client:
            uploads = []
            for index in range(15):
                stream = io.BytesIO()
                Image.new("RGB", (8, 8), (index, 90, 30)).save(stream, format="PNG")
                response = client.post("/api/skill-extractions/uploads", json={"name": f"{index}.png",
                    "data_base64": base64.b64encode(stream.getvalue()).decode()})
                self.assertEqual(response.status_code, 201, response.text)
                uploads.append(response.json()["id"])
            response = client.post("/api/skill-extractions", json={"request_id": "http-fresh-15",
                "mode": "single", "upload_ids": uploads, "instruction": "Keep every original"})
            self.assertEqual(response.status_code, 202, response.text)
            self.service.worker.join(15)
            endpoint = "/api/skill-extractions/" + response.json()["id"]
            for _ in range(3):
                current = client.get(endpoint).json()
                self.assertEqual(current["status"], "ready", current.get("error"))
                self.assertEqual(len([item for item in current["files"] if item["path"].startswith("assets/")]), 15)
            self.assertEqual(sdk.turn_count, 1)
            self.assertEqual(current["saved_skills"], [])

    def test_interrupted_turn_cannot_apply_an_otherwise_valid_manifest(self):
        sdk = ControlledSdk(manifest())
        sdk.status = "interrupted"
        with patch("creatoros.integrations.skill_extraction._production_client", sdk.client), \
             patch("creatoros.integrations.skill_extraction.collect_observed_turn", sdk.collect):
            job = self.service.submit("interrupted-manifest", source_text="Example")
            self.service.worker.join(15)
        current = self.service.get(job["id"])
        self.assertEqual(current["status"], "interrupted")
        self.assertEqual(current["revision"], 0)
        self.assertEqual(sdk.turn_count, 1)
        self.assertFalse((self.service._path("jobs", job["id"]) / "draft_delivery.json").exists())

    def test_explicit_delete_portable_paths_and_limits_preserve_previous_draft_on_error(self):
        from creatoros.integrations import skill_draft_delivery as delivery
        directory = self.operation("patch-tree")
        source = directory / "draft/skill"
        (source / "references").mkdir(parents=True)
        (source / "SKILL.md").write_text(document("color-labels"), encoding="utf-8")
        (source / "references/old.md").write_text("Delete only when explicitly requested", encoding="utf-8")
        reply = manifest()
        reply["skills"][0]["files"] = [{"path": "SKILL.md", "text": document("color-labels")},
            {"path": "scripts\\helper.py", "text": "# Draft only; never execute\n"}]
        reply["skills"][0]["delete_paths"] = ["references/old.md"]
        result = apply_delivery(directory, "single", json.dumps(reply), [], "text", Event())
        self.assertFalse((result._directories["legacy_end_to_end"] / "references/old.md").exists())
        self.assertIn("never execute", (result._directories["legacy_end_to_end"] / "scripts/helper.py").read_text())
        with patch.object(delivery, "MAX_CONTEXT_TEXT_BYTES", 10), self.assertRaises(ValueError):
            existing_context(directory, "single")
        limited = self.operation("limited-output")
        with patch.object(delivery, "MAX_LOCAL_SKILL_BYTES", 20), self.assertRaises(ValueError):
            apply_delivery(limited, "single", json.dumps(manifest()), [], "text", Event())
        self.assertEqual(list((limited / "draft").iterdir()), [])
        too_many = manifest()
        too_many["skills"][0]["files"].extend({"path": f"references/{i}.txt", "text": ""} for i in range(2000))
        with self.assertRaises(ValueError):
            apply_delivery(self.operation("too-many"), "single", json.dumps(too_many), [], "text", Event())

    def test_reference_copy_collision_rejects_without_overwriting_old_asset(self):
        sdk = ControlledSdk(manifest())
        image_id, original_bytes = self.upload(40)
        with patch("creatoros.integrations.skill_extraction._production_client", sdk.client), \
             patch("creatoros.integrations.skill_extraction.collect_observed_turn", sdk.collect):
            job = self.service.submit("legacy-asset-collision", [image_id])
            self.service.worker.join(15)
            initial = self.service.get(job["id"])
            source = self.service._draft_root(initial) / "legacy_end_to_end"
            collision = source / "assets/reference-01.png"
            collision.rename(source / "assets/original-preserved.png")
            # A legacy candidate can retain the original under another name.
            # Its existing named asset must not silently be replaced on revision.
            _other_id, existing_bytes = self.upload(41)
            collision.write_bytes(existing_bytes)
            from creatoros.integrations.producer_skills import _digest
            baseline_digest = _digest(self.service._draft_root(initial))
            self.service._update(job["id"], digest=baseline_digest)
            self.service.revise(job["id"], "prose-revision-with-collision", baseline_digest, "Only improve prose")
            self.service.worker.join(15)
        current = self.service.get(job["id"])
        self.assertEqual(current["revision"], 1)
        self.assertEqual(current["digest"], baseline_digest)
        self.assertIn("冲突", current["error"])
        self.assertEqual(collision.read_bytes(), existing_bytes)
        self.assertEqual((source / "assets/original-preserved.png").read_bytes(), original_bytes)
        self.assertEqual(sdk.turn_count, 2)

    def test_non_mind_source_text_is_retained_as_original_asset(self):
        directory = self.operation("source-text")
        (directory / "source.txt").write_text("Original sample wording 原始文案", encoding="utf-8")
        result = apply_delivery(directory, "single", json.dumps(manifest()), [], "text", Event())
        self.assertEqual((result._directories["legacy_end_to_end"] / "assets/source.txt").read_text(encoding="utf-8"),
                         "Original sample wording 原始文案")

    def test_sensitive_existing_files_are_not_sent_or_modified(self):
        directory = self.operation("sensitive-existing")
        source = directory / "draft/skill"
        source.mkdir()
        (source / "SKILL.md").write_text(document("color-labels"), encoding="utf-8")
        (source / "secrets.json").write_text('{"token":"DO_NOT_SEND"}', encoding="utf-8")
        context = existing_context(directory, "single")
        self.assertNotIn("DO_NOT_SEND", json.dumps(context))
        self.assertIn("sensitive_path_not_sent_to_model", json.dumps(context))
        result = apply_delivery(directory, "single", json.dumps(manifest()), [], "text", Event())
        self.assertIn("DO_NOT_SEND", (result._directories["legacy_end_to_end"] / "secrets.json").read_text())
        reply = manifest()
        reply["skills"][0]["files"].append({"path": "secrets.json", "text": "replace"})
        with self.assertRaisesRegex(ValueError, "敏感"):
            apply_delivery(directory, "single", json.dumps(reply), [], "text", Event())


if __name__ == "__main__":
    unittest.main()
