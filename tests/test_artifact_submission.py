"""Real temporary files, fault-injected SDK image events; no paid calls."""
import json
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from creatoros.integrations.artifact_submission import ImageSubmission, SUBMISSION, accepted_submission
from creatoros.integrations.native_production import verified_pages
from tests import test_file_delivery as file_fixtures


class ArtifactSubmissionTests(unittest.TestCase):
    setUp = file_fixtures.FileDeliveryTests.setUp
    receive = file_fixtures.FileDeliveryTests.receive
    page = staticmethod(file_fixtures.FileDeliveryTests.page)

    def handler(self):
        self.checkpoint.handoff_protocol = "tool-v1"
        handler = ImageSubmission(self.directory, self.checkpoint, self.generated, self.bundle)
        handler.turn_id = "turn-1"
        return handler

    def payload(self, path):
        return {"title": "图文标题", "text": "文案\n\n保留换行", "hashtags": ["英语"],
                "complete": True, "artifacts": [self.page(1, source_image_path=str(path))]}

    def test_wrong_path_then_correct_same_handler_preserves_one_image(self):
        from creatoros.delivery.image import SUBMISSION_INTENT
        asset, path = self.receive("first", (10, 80, 120))
        handler = self.handler()
        bad = handler.submit(self.payload(path.with_name("missing.png")))
        self.assertFalse(bad["accepted"])
        self.assertTrue(bad["retryable"])
        self.assertEqual(bad["received_images"][0]["source_image_path"], str(path))
        self.assertFalse((self.directory / SUBMISSION).exists())
        self.assertTrue((self.directory / SUBMISSION_INTENT).is_file())
        self.assertTrue(self.handler().submit(self.payload(path))["outcome_unknown"])
        good = handler.submit(self.payload(path))
        self.assertTrue(good["accepted"])
        self.assertEqual(len(self.store.read().assets), 1)
        self.assertEqual(self.checkpoint.pages[0].asset_id, asset.asset_id)
        self.assertFalse(self.checkpoint.turn_completed, "Tool success is not SDK turn completion")
        self.assertEqual(accepted_submission(self.directory, self.checkpoint)["turn_id"], "turn-1")

    def test_duplicate_success_reuses_durable_receipt(self):
        _, path = self.receive("first", (10, 80, 120))
        handler = self.handler()
        first = handler.submit(self.payload(path))
        before = (self.directory / SUBMISSION).read_bytes()
        self.assertEqual(handler.submit(self.payload(path)), first)
        self.assertEqual((self.directory / SUBMISSION).read_bytes(), before)
        second_handler = self.handler()
        second_handler.turn_id = "turn-2"
        self.assertTrue(second_handler.submit(self.payload(path))["outcome_unknown"])
        from creatoros.integrations.worker_protocol import record_thread, record_turn
        from creatoros.integrations.visual_production import atomic_json
        from creatoros.delivery.image import CHECKPOINT
        record_thread(self.directory, self.checkpoint.thread_id)
        record_turn(self.directory, "turn-1", "production", "completed")
        self.checkpoint.turn_completed = True
        atomic_json(self.directory / CHECKPOINT, self.checkpoint)
        self.assertEqual(self.handler().submit(self.payload(path)), first)

    def test_read_only_inspection_requires_matching_completed_turn(self):
        from creatoros.delivery.image_submission_check import inspect_submission
        from creatoros.integrations.worker_protocol import record_thread, record_turn
        _, path = self.receive("first", (10, 80, 120))
        self.handler().submit(self.payload(path))
        record_thread(self.directory, self.checkpoint.thread_id)
        record_turn(self.directory, "other-turn", "production", "completed")
        self.assertEqual(inspect_submission(self.directory)["status"], "unconfirmed")
        record_turn(self.directory, "turn-1", "production", "completed")
        self.assertEqual(inspect_submission(self.directory)["status"], "unconfirmed")
        from creatoros.integrations.visual_production import atomic_json
        from creatoros.delivery.image import CHECKPOINT
        self.checkpoint.turn_completed = True
        atomic_json(self.directory / CHECKPOINT, self.checkpoint)
        before = {p: p.read_bytes() for p in self.directory.rglob("*") if p.is_file()}
        inspection = inspect_submission(self.directory)
        self.assertEqual(inspection["status"], "completed_evidence")
        self.assertTrue(inspection["receipt_matches_checkpoint"])
        self.assertEqual(before, {p: p.read_bytes() for p in self.directory.rglob("*") if p.is_file()})

    def test_different_payload_cannot_replace_accepted_delivery(self):
        _, path = self.receive("first", (10, 80, 120))
        handler = self.handler()
        handler.submit(self.payload(path))
        changed = {**self.payload(path), "title": "changed"}
        result = handler.submit(changed)
        self.assertFalse(result["accepted"])
        self.assertFalse(result["retryable"])
        self.assertEqual(self.checkpoint.delivery.title, "图文标题")

    def test_three_invalid_submissions_stop_without_deleting_image(self):
        _, path = self.receive("first", (10, 80, 120))
        handler = self.handler()
        for index in range(3):
            result = handler.submit(self.payload(path.with_name("missing.png")))
            self.assertEqual(result["retryable"], index < 2)
        self.assertFalse(handler.submit(self.payload(path))["accepted"])
        self.assertTrue(path.is_file())
        self.assertEqual(len(self.store.read().assets), 1)

    def test_host_receipt_tampering_is_rejected(self):
        _, path = self.receive("first", (10, 80, 120))
        self.handler().submit(self.payload(path))
        target = self.directory / SUBMISSION
        data = json.loads(target.read_text(encoding="utf-8"))
        data["submission"]["title"] = "forged"
        target.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(ValueError):
            verified_pages(self.directory, self.checkpoint)

    def test_submission_requires_file_paths_not_model_internal_ids(self):
        asset, path = self.receive("first", (10, 80, 120))
        value = self.payload(path)
        value["artifacts"] = [self.page(1, asset_id=asset.asset_id)]
        self.assertFalse(self.handler().submit(value)["accepted"])

    def test_failed_receipt_checkpoint_write_cannot_acknowledge_in_memory_success(self):
        _, path = self.receive("first", (10, 80, 120))
        handler = self.handler()
        with patch("creatoros.integrations.visual_production.atomic_json", side_effect=OSError("disk full")):
            first = handler.submit(self.payload(path))
            second = handler.submit(self.payload(path))
        self.assertFalse(first["accepted"])
        self.assertFalse(second["accepted"])
        self.assertIsNone(self.checkpoint.submission_sha256)
        self.assertTrue(handler.submit(self.payload(path)).get("outcome_unknown"))

    def test_partial_receipt_write_latches_and_never_overwrites(self):
        _, path = self.receive("first", (10, 80, 120))
        handler = self.handler()
        from creatoros.integrations.visual_production import atomic_json as real_atomic_json
        def fail_final(target, checkpoint):
            if checkpoint.submission_sha256:
                raise OSError("failure after receipt saved")
            return real_atomic_json(target, checkpoint)
        with patch("creatoros.integrations.visual_production.atomic_json", side_effect=fail_final):
            first = handler.submit(self.payload(path))
        self.assertTrue(first.get("outcome_unknown"))
        original = (self.directory / SUBMISSION).read_bytes()
        second = handler.submit({**self.payload(path), "title": "different"})
        self.assertTrue(second.get("outcome_unknown"))
        self.assertEqual((self.directory / SUBMISSION).read_bytes(), original)
        self.assertTrue(path.exists())

    def test_final_checkpoint_write_then_error_latches_unknown(self):
        _, path = self.receive("first", (10, 80, 120))
        handler = self.handler()
        from creatoros.integrations.visual_production import atomic_json as real_atomic_json
        def commit_then_fail(target, checkpoint):
            real_atomic_json(target, checkpoint)
            if checkpoint.submission_sha256:
                raise OSError("write completed but response unknown")
        with patch("creatoros.integrations.visual_production.atomic_json", side_effect=commit_then_fail):
            first = handler.submit(self.payload(path))
        self.assertTrue(first.get("outcome_unknown"))
        original = (self.directory / SUBMISSION).read_bytes()
        self.assertTrue(handler.submit({**self.payload(path), "title": "different"}).get("outcome_unknown"))
        self.assertEqual((self.directory / SUBMISSION).read_bytes(), original)

    def test_previous_completed_flag_cannot_certify_new_submission(self):
        from creatoros.delivery.image import CHECKPOINT, Checkpoint
        from creatoros.integrations.visual_production import atomic_json
        from creatoros.integrations.worker_protocol import record_thread, record_turn
        from creatoros.delivery.image_submission_check import inspect_submission
        _, path = self.receive("first", (10, 80, 120))
        self.checkpoint.turn_completed = True
        handler = self.handler()
        def commit_then_fail(target, value):
            atomic_json(target, value)
            if value.submission_sha256:
                raise OSError("committed then failed")
        with patch("creatoros.integrations.visual_production.atomic_json", side_effect=commit_then_fail):
            self.assertTrue(handler.submit(self.payload(path))["outcome_unknown"])
        persisted = Checkpoint.model_validate_json((self.directory / CHECKPOINT).read_text(encoding="utf-8"))
        self.assertFalse(persisted.turn_completed)
        record_thread(self.directory, persisted.thread_id)
        record_turn(self.directory, "turn-1", "production", "completed")
        self.assertEqual(inspect_submission(self.directory)["status"], "unconfirmed")

    def test_receipt_write_then_error_blocks_new_handler_and_preserves_evidence(self):
        from creatoros.integrations.artifact_submission import atomic_write_text
        from creatoros.delivery.image_submission_check import inspect_submission
        _, path = self.receive("first", (10, 80, 120))
        handler = self.handler()
        def written_then_error(target, raw):
            atomic_write_text(target, raw)
            raise OSError("receipt replaced but result lost")
        with patch("creatoros.integrations.artifact_submission.atomic_write_text", side_effect=written_then_error):
            self.assertTrue(handler.submit(self.payload(path)).get("outcome_unknown"))
        original = (self.directory / SUBMISSION).read_bytes()
        self.assertTrue(self.handler().submit({**self.payload(path), "title": "different"}).get("outcome_unknown"))
        before = {p: p.read_bytes() for p in self.directory.rglob("*") if p.is_file()}
        self.assertEqual(inspect_submission(self.directory)["status"], "unconfirmed")
        self.assertEqual(before, {p: p.read_bytes() for p in self.directory.rglob("*") if p.is_file()})
        self.assertEqual((self.directory / SUBMISSION).read_bytes(), original)
        self.assertTrue(path.is_file())

    def test_partial_intent_write_blocks_retry(self):
        from creatoros.delivery.image import SUBMISSION_INTENT
        from creatoros.delivery.image_submission_check import inspect_submission
        _, path = self.receive("first", (10, 80, 120))
        handler = self.handler()
        with patch("creatoros.integrations.artifact_submission.os.fsync", side_effect=OSError("fsync failed")):
            self.assertTrue(handler.submit(self.payload(path)).get("outcome_unknown"))
        self.assertTrue((self.directory / SUBMISSION_INTENT).exists())
        self.assertEqual(inspect_submission(self.directory)["status"], "unconfirmed")
        self.assertTrue(self.handler().submit(self.payload(path)).get("outcome_unknown"))
        self.assertTrue(path.is_file())

    def test_partial_receipt_bytes_are_never_replaced_after_restart(self):
        from creatoros.delivery.image_submission_check import inspect_submission
        _, path = self.receive("first", (10, 80, 120))
        def partial_then_error(target, raw):
            target.write_text(raw[:30], encoding="utf-8")
            raise OSError("partial receipt")
        with patch("creatoros.integrations.artifact_submission.atomic_write_text", side_effect=partial_then_error):
            self.assertTrue(self.handler().submit(self.payload(path)).get("outcome_unknown"))
        before = (self.directory / SUBMISSION).read_bytes()
        self.assertEqual(inspect_submission(self.directory)["status"], "unconfirmed")
        self.assertTrue(self.handler().submit(self.payload(path)).get("outcome_unknown"))
        self.assertEqual((self.directory / SUBMISSION).read_bytes(), before)
        self.assertTrue(path.is_file())

    def test_validation_does_not_remove_another_owner_intent(self):
        from creatoros.delivery.image import SUBMISSION_INTENT
        from creatoros.integrations.artifact_submission import FileDelivery
        _, path = self.receive("first", (10, 80, 120))
        handler = self.handler()
        marker = self.directory / SUBMISSION_INTENT
        def race_then_reject(*args, **kwargs):
            marker.write_text("other owner", encoding="utf-8")
            raise ValueError("invalid")
        with patch.object(FileDelivery, "model_validate", side_effect=race_then_reject):
            self.assertFalse(handler.submit(self.payload(path))["accepted"])
        self.assertEqual(marker.read_text(encoding="utf-8"), "other owner")

    def test_unknown_before_during_after_correction_marker_update_blocks_rebuilt_handler(self):
        from creatoros.delivery.image import SUBMISSION_INTENT
        from creatoros.delivery.image_submission_check import inspect_submission
        from creatoros.delivery.image_recovery import ImageSubmissionUnconfirmed, recover_checkpoint
        from creatoros.integrations.artifact_submission import json as submission_json
        real_dump = submission_json.dump
        for phase in ("before", "during", "after"):
            with self.subTest(phase=phase):
                fixture = file_fixtures.FileDeliveryTests()
                fixture.setUp()
                try:
                    _, path = fixture.receive("first", (10, 80, 120))
                    fixture.checkpoint.handoff_protocol = "tool-v1"
                    handler = ImageSubmission(fixture.directory, fixture.checkpoint, fixture.generated, fixture.bundle)
                    handler.turn_id = "turn-1"
                    def payload(source):
                        return {"title": "Title", "text": "Caption", "hashtags": [], "complete": True,
                                "artifacts": [fixture.page(1, source_image_path=str(source))]}
                    self.assertTrue(handler.submit(payload(path.with_name("missing.png")))["retryable"])
                    marker = fixture.directory / SUBMISSION_INTENT
                    self.assertTrue(marker.is_file())
                    def interrupt_update(value, stream, **kwargs):
                        if phase == "during":
                            stream.write('{"partial":')
                            handler.mark_unknown()
                        real_dump(value, stream, **kwargs)
                        if phase == "after":
                            handler.mark_unknown()
                    if phase == "before":
                        handler.mark_unknown()
                    with patch("creatoros.integrations.artifact_submission.json.dump", side_effect=interrupt_update):
                        result = handler.submit(payload(path))
                    self.assertTrue(result["outcome_unknown"])
                    self.assertTrue(marker.is_file())
                    self.assertEqual(inspect_submission(fixture.directory)["status"], "unconfirmed")
                    receipt_path = fixture.directory / SUBMISSION
                    receipt_before = receipt_path.read_bytes() if receipt_path.exists() else None
                    new_handler = ImageSubmission(fixture.directory, fixture.checkpoint, fixture.generated, fixture.bundle)
                    new_handler.turn_id = "turn-2"
                    self.assertTrue(new_handler.submit(payload(path))["outcome_unknown"])
                    self.assertEqual(receipt_path.read_bytes() if receipt_path.exists() else None, receipt_before)
                    next_attempt = fixture.root / "attempt-002"
                    next_attempt.mkdir()
                    with self.assertRaises(ImageSubmissionUnconfirmed):
                        recover_checkpoint(next_attempt, fixture.checkpoint.input_digest, fixture.generated)
                    self.assertTrue(path.is_file())
                finally:
                    fixture.doCleanups()


class SubmissionExecutionTests(unittest.TestCase):
    def _adapter_unknown_case(self, cause):
        from threading import Event, Thread
        from time import monotonic
        from openai_codex.generated import v2_all as sdk_types
        from openai_codex.models import Notification
        from creatoros.integrations.codex import CodexSdkProducer, CodexProducerError, _DynamicToolClient
        from creatoros.integrations.artifact_submission import atomic_write_text
        from creatoros.delivery.image_submission_check import inspect_submission
        from tests.smoke_native_production import image, write_skill
        with TemporaryDirectory(prefix="real-adapter-unknown-") as temporary:
            root = Path(temporary)
            skill, generated = root / "skill", root / "generated"
            write_skill(skill, "submit-fixture", "Test receipt")
            path = generated / "submit-thread" / "image.png"
            payload = {"title": "Title", "text": "Caption", "hashtags": [], "complete": True,
                       "artifacts": [{"order": 1, "source_image_path": str(path), "image_prompt": "Scene",
                                      "reference_ids": [], "content": "text"}]}
            writing, adapter_done, release, cancel = Event(), Event(), Event(), Event()
            replies, calls, errors = [], [], []
            def block_write(target, raw):
                if target.name == SUBMISSION:
                    writing.set()
                    if not release.wait(15):
                        raise RuntimeError("adapter fixture release timed out")
                atomic_write_text(target, raw)
            def block_validation(*args, **kwargs):
                writing.set()
                if not release.wait(15):
                    raise RuntimeError("adapter fixture release timed out")
                raise ValueError("injected validation failure after index mutation")
            def control():
                try:
                    if not writing.wait(15):
                        raise RuntimeError("handler did not reach receipt write")
                    if cause in {"cancel", "validation", "not_started"}:
                        cancel.set()
                    if not adapter_done.wait(15):
                        raise RuntimeError("adapter did not report unknown")
                except Exception as error:
                    errors.append(str(error))
                finally:
                    release.set()
            @asynccontextmanager
            async def client(deadline, cancel_event, dynamic_tools):
                if cause == "not_started":
                    from dataclasses import replace
                    original_handler = dynamic_tools[0].handler
                    async def delayed_handler(arguments):
                        writing.set()
                        await asyncio.to_thread(release.wait, 15)
                        return await original_handler(arguments)
                    dynamic_tools = [replace(dynamic_tools[0], handler=delayed_handler)]
                raw = SimpleNamespace(_client=SimpleNamespace(_sync=SimpleNamespace(_approval_handler=lambda *args: {})))
                adapter = _DynamicToolClient(raw, dynamic_tools, deadline, cancel_event)
                adapter.thread_id = "submit-thread"
                def invoke():
                    try:
                        if cause == "timeout":
                            adapter.deadline = monotonic() + 1
                        return adapter._handle("item/tool/call", {"tool": "submit_artifacts", "namespace": None,
                            "threadId": "submit-thread", "turnId": "turn-1", "callId": "call-1", "arguments": payload})
                    finally:
                        adapter_done.set()
                async def start(*args, **options):
                    async def turn(inputs, **controls):
                        calls.append(1)
                        async def stream():
                            image(path)
                            yield Notification("item/completed", sdk_types.ItemCompletedNotification.model_validate({
                                "threadId": "submit-thread", "turnId": "turn-1", "completedAtMs": 1,
                                "item": {"type": "imageGeneration", "id": "image-1", "status": "completed",
                                         "result": "", "savedPath": str(path)}}))
                            replies.append(await asyncio.to_thread(invoke))
                            yield Notification("turn/completed", sdk_types.TurnCompletedNotification.model_validate({
                                "threadId": "submit-thread", "turn": {"id": "turn-1", "items": [], "status": "completed"}}))
                        async def interrupt():
                            pass
                        return SimpleNamespace(id="turn-1", thread_id="submit-thread", stream=stream, interrupt=interrupt)
                    return SimpleNamespace(id="submit-thread", turn=turn)
                try:
                    yield SimpleNamespace(thread_start=start, thread_resume=start)
                finally:
                    adapter.close_handler()
            import asyncio
            producer = CodexSdkProducer(project_root=root, generated_images_root=generated, timeout_seconds=20)
            def produce(n, event=None):
                return producer.produce_to(directory=root / "run" / "revision-001" / f"attempt-{n:03d}",
                    pack_id="pack", creator_id="creator", series_id="series", topic_id="topic", topic_title="Topic",
                    skill_directory=skill, skill_name="submit-fixture", production_protocol="native-v1", cancel_event=event)
            controller = Thread(target=control)
            controller.start()
            try:
                fault = (patch("creatoros.delivery.image.ingest", side_effect=block_validation) if cause == "validation"
                         else patch("creatoros.integrations.artifact_submission.atomic_write_text", side_effect=block_write))
                with patch("creatoros.integrations.native_production._production_client", client), fault:
                    with self.assertRaises(CodexProducerError) as failure:
                        produce(1, cancel)
                self.assertEqual(failure.exception.error_type, "image_delivery_unconfirmed")
            finally:
                release.set()
                controller.join(20)
            self.assertFalse(controller.is_alive())
            self.assertEqual(errors, [])
            self.assertTrue(json.loads(replies[0]["contentItems"][0]["text"])["outcome_unknown"])
            original = root / "run" / "revision-001" / "attempt-001"
            self.assertTrue(path.is_file())
            self.assertEqual(inspect_submission(original)["status"], "unconfirmed")
            before = {p.relative_to(original): p.read_bytes() for p in original.rglob("*") if p.is_file()}
            with patch("creatoros.integrations.native_production._production_client", side_effect=AssertionError("unexpected SDK")) as sdk:
                with self.assertRaises(CodexProducerError) as retry:
                    produce(2)
            self.assertEqual(retry.exception.error_type, "image_delivery_unconfirmed")
            sdk.assert_not_called()
            self.assertEqual(calls, [1])
            self.assertEqual(before, {p.relative_to(original): p.read_bytes() for p in original.rglob("*") if p.is_file()})

    def test_real_adapter_cancel_during_submission_is_unknown(self):
        self._adapter_unknown_case("cancel")

    def test_real_adapter_timeout_during_submission_is_unknown(self):
        self._adapter_unknown_case("timeout")

    def test_real_adapter_unknown_then_validation_error_preserves_guard(self):
        self._adapter_unknown_case("validation")

    def test_real_adapter_unknown_before_image_handler_starts_persists_guard(self):
        self._adapter_unknown_case("not_started")

    def test_interrupted_recovery_keeps_intent_and_third_attempt_never_dispatches(self):
        from openai_codex.generated import v2_all as sdk_types
        from openai_codex.models import Notification
        from creatoros.integrations.codex import CodexSdkProducer, CodexProducerError
        from creatoros.delivery.image import SUBMISSION_INTENT
        from creatoros.delivery.image_submission_check import inspect_submission
        from creatoros.delivery.image_recovery import atomic_json
        from tests.smoke_native_production import image, write_skill
        with TemporaryDirectory(prefix="submission-recovery-") as temporary:
            root = Path(temporary)
            skill, generated = root / "skill", root / "generated"
            write_skill(skill, "submit-fixture", "Test receipt")
            path = generated / "thread" / "image.png"
            payload = {"title": "Title", "text": "Caption", "hashtags": [], "complete": True,
                       "artifacts": [{"order": 1, "source_image_path": str(path), "image_prompt": "Scene",
                                      "reference_ids": [], "content": "text"}]}
            calls = []
            @asynccontextmanager
            async def client(deadline, cancel_event, dynamic_tools):
                handler = dynamic_tools[0].handler
                async def start(*args, **options):
                    async def turn(inputs, **controls):
                        calls.append(1)
                        async def stream():
                            image(path)
                            yield Notification("item/completed", sdk_types.ItemCompletedNotification.model_validate({
                                "threadId": "thread", "turnId": "turn-1", "completedAtMs": 1,
                                "item": {"type": "imageGeneration", "id": "image-1", "status": "completed",
                                         "result": "", "savedPath": str(path)}}))
                            self.assertTrue((await handler(payload))["accepted"])
                            yield Notification("turn/completed", sdk_types.TurnCompletedNotification.model_validate({
                                "threadId": "thread", "turn": {"id": "turn-1", "items": [], "status": "completed"}}))
                        async def interrupt():
                            pass
                        return SimpleNamespace(id="turn-1", thread_id="thread", stream=stream, interrupt=interrupt)
                    return SimpleNamespace(id="thread", turn=turn)
                yield SimpleNamespace(thread_start=start, thread_resume=start)
                raise OSError("SDK process exited after confirmed turn")
            producer = CodexSdkProducer(project_root=root, generated_images_root=generated, timeout_seconds=10)
            def produce(index):
                return producer.produce_to(directory=root / "run" / "revision-001" / f"attempt-{index:03d}",
                    pack_id="pack", creator_id="creator", series_id="series", topic_id="topic", topic_title="Topic",
                    skill_directory=skill, skill_name="submit-fixture", production_protocol="native-v1")
            with patch("creatoros.integrations.native_production._production_client", client):
                with self.assertRaises(CodexProducerError):
                    produce(1)
                first = root / "run" / "revision-001" / "attempt-001"
                self.assertEqual(inspect_submission(first)["status"], "completed_evidence")
                before = {p.relative_to(first): p.read_bytes() for p in first.rglob("*") if p.is_file()}
                from creatoros.delivery.image import CHECKPOINT
                def checkpoint_then_error(target, value):
                    atomic_json(target, value)
                    if target.name == CHECKPOINT:
                        raise OSError("recovery checkpoint committed then failure")
                with patch("creatoros.delivery.image_recovery.atomic_json", side_effect=checkpoint_then_error):
                    with self.assertRaises(OSError):
                        produce(2)
                second = first.parent / "attempt-002"
                self.assertTrue((second / SUBMISSION_INTENT).is_file())
                self.assertEqual(inspect_submission(second)["status"], "completed_evidence")
                produce(3)  # Recovery may finish offline; it must not create a new SDK turn.
                with patch("creatoros.delivery.image_recovery.ImageAssetStore.restore_from", side_effect=OSError("recovery interrupted")):
                    with self.assertRaises(OSError):
                        produce(4)
                fourth = first.parent / "attempt-004"
                self.assertTrue((fourth / SUBMISSION_INTENT).is_file())
                self.assertEqual(inspect_submission(fourth)["status"], "unconfirmed")
                with self.assertRaises(CodexProducerError) as failure:
                    produce(5)
                self.assertEqual(failure.exception.error_type, "image_delivery_unconfirmed")
            self.assertEqual(len(calls), 1)
            self.assertTrue(path.is_file())
            self.assertEqual(before, {p.relative_to(first): p.read_bytes() for p in first.rglob("*") if p.is_file()})

    def test_uncertain_submission_interrupts_turn_without_repair(self):
        from threading import Event
        from openai_codex.generated import v2_all as sdk_types
        from openai_codex.models import Notification
        from creatoros.integrations.codex import CodexSdkProducer, CodexProducerError
        from creatoros.integrations.visual_production import atomic_json
        from creatoros.integrations.worker_protocol import record_thread, record_turn
        from creatoros.delivery.image_submission_check import inspect_submission
        from tests.smoke_native_production import image, write_skill
        with TemporaryDirectory(prefix="submission-unknown-") as temporary:
            root = Path(temporary)
            skill, generated = root / "skill", root / "generated"
            write_skill(skill, "submit-fixture", "Test receipt")
            image_path = generated / "submit-thread" / "image.png"
            payload = {"title": "Title", "text": "Caption", "hashtags": [], "complete": True,
                       "artifacts": [{"order": 1, "source_image_path": str(image_path),
                                      "image_prompt": "A scene", "reference_ids": [], "content": "text"}]}
            calls, interrupts, replies = [], [], []
            cancel = Event()
            @asynccontextmanager
            async def client(deadline, cancel_event, dynamic_tools):
                handler = dynamic_tools[0].handler
                async def start(*args, **options):
                    async def turn(inputs, **controls):
                        calls.append(1)
                        async def stream():
                            image(image_path)
                            yield Notification("item/completed", sdk_types.ItemCompletedNotification.model_validate({
                                "threadId": "submit-thread", "turnId": "turn-1", "completedAtMs": 1,
                                "item": {"type": "imageGeneration", "id": "image-1", "status": "completed",
                                         "result": "", "savedPath": str(image_path)}}))
                            replies.append(await handler(payload))
                            cancel.set()  # Cancellation and unknown result become visible together.
                            yield Notification("turn/completed", sdk_types.TurnCompletedNotification.model_validate({
                                "threadId": "submit-thread", "turn": {"id": "turn-1", "items": [], "status": "completed"}}))
                        async def interrupt():
                            interrupts.append(1)
                        return SimpleNamespace(id="turn-1", thread_id="submit-thread", stream=stream, interrupt=interrupt)
                    return SimpleNamespace(id="submit-thread", turn=turn)
                yield SimpleNamespace(thread_start=start, thread_resume=start)
            def written_then_error(target, value):
                atomic_json(target, value)
                if value.submission_sha256:
                    raise OSError("lost checkpoint acknowledgment")
            producer = CodexSdkProducer(project_root=root, generated_images_root=generated, timeout_seconds=10)
            with patch("creatoros.integrations.native_production._production_client", client), patch(
                    "creatoros.integrations.visual_production.atomic_json", side_effect=written_then_error):
                with self.assertRaises(CodexProducerError) as failure:
                    producer.produce_to(directory=root / "run" / "revision-001" / "attempt-001",
                        pack_id="pack", creator_id="creator", series_id="series", topic_id="topic", topic_title="Topic",
                        skill_directory=skill, skill_name="submit-fixture", production_protocol="native-v1",
                        cancel_event=cancel)
            self.assertEqual(failure.exception.error_type, "image_delivery_unconfirmed")
            self.assertTrue(replies[0]["outcome_unknown"])
            self.assertEqual((len(calls), len(interrupts)), (1, 1))
            self.assertTrue(image_path.is_file())
            original = root / "run" / "revision-001" / "attempt-001"
            self.assertTrue((original / SUBMISSION).is_file())
            record_thread(original, "submit-thread")
            record_turn(original, "turn-1", "production", "completed")
            before = {p.relative_to(original): p.read_bytes() for p in original.rglob("*") if p.is_file()}
            self.assertEqual(inspect_submission(original)["status"], "unconfirmed")
            with patch("creatoros.integrations.native_production._production_client", client):
                with self.assertRaises(CodexProducerError) as restart:
                    producer.produce_to(directory=root / "run" / "revision-001" / "attempt-002",
                        pack_id="pack", creator_id="creator", series_id="series", topic_id="topic", topic_title="Topic",
                        skill_directory=skill, skill_name="submit-fixture", production_protocol="native-v1")
            self.assertEqual(restart.exception.error_type, "image_delivery_unconfirmed")
            self.assertEqual(len(calls), 1)
            self.assertEqual(before, {p.relative_to(original): p.read_bytes() for p in original.rglob("*") if p.is_file()})

    def test_checkpoint_committed_then_error_and_completed_worker_cannot_resume(self):
        from creatoros.delivery.image_submission_check import inspect_submission
        from creatoros.integrations.worker_protocol import record_thread, record_turn
        from creatoros.integrations.visual_production import atomic_json
        from tests import test_file_delivery as fixtures
        fixture = fixtures.FileDeliveryTests()
        fixture.setUp()
        try:
            asset, path = fixture.receive("first", (10, 80, 120))
            checkpoint = fixture.checkpoint
            checkpoint.handoff_protocol = "tool-v1"
            handler = ImageSubmission(fixture.directory, checkpoint, fixture.generated, fixture.bundle)
            handler.turn_id = "turn-1"
            payload = {"title": "Title", "text": "Caption", "hashtags": [], "complete": True,
                       "artifacts": [fixture.page(1, source_image_path=str(path))]}
            def committed_then_error(target, value):
                atomic_json(target, value)
                if value.submission_sha256:
                    raise OSError("checkpoint committed but acknowledgement lost")
            with patch("creatoros.integrations.visual_production.atomic_json", side_effect=committed_then_error):
                self.assertTrue(handler.submit(payload)["outcome_unknown"])
            record_thread(fixture.directory, checkpoint.thread_id)
            record_turn(fixture.directory, "turn-1", "production", "completed")
            before = {p: p.read_bytes() for p in fixture.directory.rglob("*") if p.is_file()}
            self.assertEqual(inspect_submission(fixture.directory)["status"], "unconfirmed")
            self.assertEqual(before, {p: p.read_bytes() for p in fixture.directory.rglob("*") if p.is_file()})
            self.assertTrue(path.is_file())
        finally:
            fixture.doCleanups()

    def test_same_turn_correction_and_interrupted_receipt_resume_without_regeneration(self):
        from openai_codex.generated import v2_all as sdk_types
        from openai_codex.models import Notification
        from creatoros.integrations.codex import CodexSdkProducer, CodexProducerError
        from creatoros.integrations.native_production import load_checkpoint
        from tests.smoke_native_production import image, write_skill
        with TemporaryDirectory(prefix="submission-execute-") as temporary:
            root = Path(temporary)
            skill = root / "skill"
            write_skill(skill, "submit-fixture", "Test receipt")
            generated = root / "generated"
            image_path = generated / "submit-thread" / "image.png"
            calls, replies, created = [], [], []
            payload = {"title": "Title", "text": "Caption", "hashtags": [], "complete": True,
                       "artifacts": [{"order": 1, "source_image_path": str(image_path),
                                      "image_prompt": "A scene", "reference_ids": [], "content": "text"}]}
            @asynccontextmanager
            async def client(deadline, cancel_event, dynamic_tools):
                handler = dynamic_tools[0].handler
                async def start(*args, **options):
                    async def turn(inputs, **controls):
                        calls.append(inputs[0].text)
                        turn_id = f"turn-{len(calls)}"
                        async def stream():
                            current_path = image_path if len(calls) < 3 else image_path.with_name("rework.png")
                            current_payload = {**payload, "artifacts": [{**payload["artifacts"][0], "source_image_path": str(current_path)}]}
                            if len(calls) in {1, 3}:
                                image(current_path)
                                created.append(current_path)
                                yield Notification("item/completed", sdk_types.ItemCompletedNotification.model_validate({
                                    "threadId": "submit-thread", "turnId": turn_id, "completedAtMs": 1,
                                    "item": {"type": "imageGeneration", "id": "image-1", "status": "completed",
                                             "result": "", "savedPath": str(current_path)}}))
                            if len(calls) == 1:
                                bad = {**payload, "artifacts": [{**payload["artifacts"][0], "source_image_path": "missing.png"}]}
                                replies.append(await handler(bad))
                            replies.append(await handler(current_payload))
                            # First turn simulates a disconnect after tool success.
                            yield Notification("turn/completed", sdk_types.TurnCompletedNotification.model_validate({
                                "threadId": "submit-thread", "turn": {"id": turn_id, "items": [],
                                    "status": "interrupted" if len(calls) == 1 else "completed"}}))
                        async def interrupt():
                            pass
                        return SimpleNamespace(id=turn_id, thread_id="submit-thread", stream=stream, interrupt=interrupt)
                    return SimpleNamespace(id="submit-thread", turn=turn)
                yield SimpleNamespace(thread_start=start, thread_resume=start)
            producer = CodexSdkProducer(project_root=root, generated_images_root=generated, timeout_seconds=10)
            def produce(attempt, revision=1, **options):
                return producer.produce_to(directory=root / "run" / f"revision-{revision:03d}" / f"attempt-{attempt:03d}",
                    pack_id="pack", creator_id="creator", series_id="series", topic_id="topic", topic_title="Topic",
                    skill_directory=skill, skill_name="submit-fixture", production_protocol="native-v1", **options)
            with patch("creatoros.integrations.native_production._production_client", client):
                with self.assertRaises(CodexProducerError):
                    produce(1)
                with self.assertRaises(CodexProducerError) as failure:
                    produce(2)
                self.assertEqual(failure.exception.error_type, "image_delivery_unconfirmed")
                self.assertEqual(len(calls), 1)
                # Model an existing legacy attempt that predates intent markers;
                # its supported receipt-only recovery behavior remains unchanged.
                (root / "run" / "revision-001" / "attempt-001" / "artifact-submission-intent.json").unlink()
                result = produce(3)
            self.assertEqual(len(created), 1)
            self.assertEqual([r["accepted"] for r in replies], [False, True, True])
            self.assertEqual(replies[1]["receipt_id"], replies[2]["receipt_id"])
            self.assertEqual(len(calls), 2)
            self.assertIn("只修交付结果", calls[1])
            checkpoint = load_checkpoint(result.directory)
            self.assertTrue(checkpoint.turn_completed)
            self.assertEqual(len(checkpoint.pages), 1)
            # An ordinary rework uses a new Revision but the same SDK thread.
            # Its persisted dynamic tool must have a fresh callback and receipt.
            with patch("creatoros.integrations.native_production._production_client", client):
                rework = produce(1, revision=2, thread_id="submit-thread")
            revised = load_checkpoint(rework.directory)
            self.assertEqual(revised.handoff_protocol, "tool-v1")
            self.assertTrue(revised.turn_completed)
            self.assertEqual(len(revised.pages), 1)
            self.assertEqual(len(created), 2)
            self.assertTrue(replies[-1]["accepted"])
            self.assertNotEqual(replies[-1]["receipt_id"], replies[1]["receipt_id"])


if __name__ == "__main__":
    unittest.main()
