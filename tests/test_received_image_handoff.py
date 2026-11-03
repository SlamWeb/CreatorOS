"""Recover missing model-visible results from real, isolated SDK receipts."""
import json
import unittest

from creatoros.integrations.native_production import submission_prompt
from tests import test_artifact_submission as fixtures


class ReceivedImageHandoffTests(unittest.TestCase):
    setUp = fixtures.ArtifactSubmissionTests.setUp
    receive = fixtures.ArtifactSubmissionTests.receive
    page = staticmethod(fixtures.ArtifactSubmissionTests.page)
    handler = fixtures.ArtifactSubmissionTests.handler
    payload = fixtures.ArtifactSubmissionTests.payload

    def received(self):
        return json.loads((self.directory / "work/generated-assets.json").read_text(encoding="utf-8"))["images"]

    def test_empty_model_result_recovers_received_image_without_original_or_regeneration(self):
        asset, source = self.receive("only-image", (10, 80, 120))
        inventory = self.store.path.read_bytes()
        # The code-mode result was empty; the only available handoff is the host view.
        source.unlink()
        result = self.handler().submit(self.payload(self.received()[0]["source_image_path"]))
        self.assertTrue(result["accepted"], result)
        self.assertEqual(self.store.path.read_bytes(), inventory)
        self.assertEqual(len(self.store.read().assets), 1)
        self.assertEqual(self.checkpoint.pages[0].asset_id, asset.asset_id)

    def test_only_received_image_is_returned_for_explicit_selection(self):
        asset, source = self.receive("only-image", (10, 80, 120))
        source.unlink()  # The SDK file can disappear after the host captured it.
        handler = self.handler()
        payload = self.payload("received:only")
        listed = handler.submit(payload)
        self.assertFalse(listed["accepted"], listed)
        self.assertTrue(listed["retryable"])
        self.assertEqual(self.checkpoint.submission_failures, 0)
        first = handler.submit(self.payload(listed["received_images"][0]["source_image_path"]))
        self.assertTrue(first["accepted"], first)
        self.assertEqual(self.checkpoint.pages[0].asset_id, asset.asset_id)
        self.assertEqual(len(self.store.read().assets), 1)

    def test_existing_accepted_only_receipt_remains_stable_if_another_event_arrives_later(self):
        import hashlib
        from creatoros.delivery.image import accepted_submission, ingest
        from creatoros.delivery.image_materials import accept_delivery_value
        from creatoros.integrations.artifact_submission import SUBMISSION, canonical
        first, path = self.receive("first", (10, 80, 120))
        handler = self.handler()
        payload = self.payload("received:only")
        # Construct the same immutable receipt format produced by the prior
        # implementation, which resolved this alias to the concrete path.
        accepted = handler.submit(self.payload(path))
        self.assertTrue(accepted["accepted"], accepted)
        receipt_path = self.directory / SUBMISSION
        state = json.loads(receipt_path.read_text(encoding="utf-8"))
        state["payload_sha256"] = hashlib.sha256(canonical(payload).encode()).hexdigest()
        raw = canonical(state)
        receipt_path.write_text(raw, encoding="utf-8")
        self.checkpoint.submission_sha256 = hashlib.sha256(raw.encode()).hexdigest()
        self.receive("late", (90, 30, 10))
        saved = accepted_submission(self.directory, self.checkpoint)
        self.assertEqual(saved["submission"]["artifacts"][0]["source_image_path"], str(path))
        accept_delivery_value(self.directory, self.checkpoint, self.generated, self.bundle,
                              saved["submission"])
        ingest(self.directory, self.checkpoint, self.generated)
        self.assertEqual(self.checkpoint.pages[0].asset_id, first.asset_id)
        self.assertEqual(handler.submit(payload), accepted)

    def test_only_received_image_refuses_ambiguous_inventory_and_keeps_existing_images(self):
        first, first_path = self.receive("first", (10, 80, 120))
        second, _ = self.receive("second", (90, 30, 10))
        handler = self.handler()
        response = handler.submit(self.payload("received:only"))
        self.assertFalse(response["accepted"])
        self.assertTrue(response["retryable"])
        self.assertEqual(len(response["received_images"]), 2)
        self.assertEqual(self.checkpoint.submission_failures, 0)
        self.assertEqual(self.checkpoint.pages, [])
        accepted = handler.submit(self.payload(first_path))
        self.assertTrue(accepted["accepted"], accepted)
        self.assertEqual(self.checkpoint.pages[0].asset_id, first.asset_id)
        self.assertEqual(len(self.store.read().assets), 2)

    def test_only_reference_waits_while_another_image_is_in_flight(self):
        from types import SimpleNamespace
        first, _ = self.receive("first", (10, 80, 120))
        handler = self.handler()
        def event(method):
            return SimpleNamespace(method=method, payload=SimpleNamespace(
                thread_id=self.checkpoint.thread_id, turn_id="turn-1",
                item=SimpleNamespace(type="imageGeneration", id="pending-image")))
        handler.observe_image_event(event("item/started"))
        response = handler.submit(self.payload("received:only"))
        self.assertFalse(response["accepted"])
        self.assertTrue(response["retryable"])
        self.assertIn("正在生成", response["error"])
        self.assertEqual(self.checkpoint.submission_failures, 0)
        self.assertEqual(self.checkpoint.pages, [])
        second, second_path = self.receive("second", (90, 30, 10))
        handler.observe_image_event(event("item/completed"))
        response = handler.submit(self.payload("received:only"))
        self.assertFalse(response["accepted"])
        self.assertEqual(len(response["received_images"]), 2)
        accepted = handler.submit(self.payload(second_path))
        self.assertTrue(accepted["accepted"], accepted)
        self.assertEqual(self.checkpoint.pages[0].asset_id, second.asset_id)

    def test_only_reference_must_not_confirm_before_queued_image_event_is_observed(self):
        import asyncio

        self.receive("first", (10, 80, 120))
        handler = self.handler()

        async def submit_while_second_event_is_queued():
            events = asyncio.Queue()
            events.put_nowait(("second", (90, 30, 10)))

            async def receive_later():
                # The SDK request adapter yields once before invoking the tool,
                # but its separate event consumer may be delayed longer.
                await asyncio.sleep(0.05)
                self.receive(*events.get_nowait())

            pending = asyncio.create_task(receive_later())
            await asyncio.sleep(0)  # Mirror the adapter's scheduling yield.
            result = await handler(self.payload("received:only"))
            await pending
            return result

        result = asyncio.run(submit_while_second_event_is_queued())
        self.assertFalse(result["accepted"], result)
        self.assertEqual(self.checkpoint.pages, [])
        self.assertEqual(len(self.store.read().assets), 2)

    def test_only_received_image_with_no_images_never_invents_a_file(self):
        response = self.handler().submit(self.payload("received:only"))
        self.assertFalse(response["accepted"])
        self.assertTrue(response["retryable"])
        self.assertEqual(response["received_images"], [])
        self.assertEqual(self.checkpoint.submission_failures, 0)
        self.assertEqual(self.checkpoint.pages, [])

    def test_only_received_image_fails_closed_if_inventory_is_invalid(self):
        self.receive("only-image", (10, 80, 120))
        self.store.path.write_text("invalid", encoding="utf-8")
        response = self.handler().submit(self.payload("received:only"))
        self.assertFalse(response["accepted"])
        self.assertFalse(response["retryable"])
        self.assertEqual(self.checkpoint.pages, [])
        import asyncio
        async_response = asyncio.run(self.handler()(self.payload("received:only")))
        self.assertFalse(async_response["accepted"])
        self.assertFalse(async_response["retryable"])

    def test_async_submission_reports_invalid_path_type_as_validation_error(self):
        import asyncio
        value = self.payload("unused")
        value["artifacts"][0]["source_image_path"] = ["not", "a", "path"]
        response = asyncio.run(self.handler()(value))
        self.assertFalse(response["accepted"])
        self.assertTrue(response["retryable"])
        self.assertEqual(self.checkpoint.pages, [])

    def test_async_submission_waits_briefly_for_the_image_event(self):
        import asyncio
        async def execute():
            async def capture():
                await asyncio.sleep(0.15)
                self.receive("only-image", (10, 80, 120))
            pending = asyncio.create_task(capture())
            result = await self.handler()(self.payload("received:only"))
            await pending
            return result
        result = asyncio.run(execute())
        self.assertFalse(result["accepted"], result)
        self.assertEqual(len(result["received_images"]), 1)
        accepted = self.handler().submit(self.payload(result["received_images"][0]["source_image_path"]))
        self.assertTrue(accepted["accepted"], accepted)
        self.assertEqual(len(self.store.read().assets), 1)

    def test_recovered_paths_keep_selected_order_and_duplicate_submission_is_idempotent(self):
        first, _ = self.receive("first", (10, 80, 120))
        second, _ = self.receive("second", (90, 30, 10))
        value = self.payload(self.received()[0]["source_image_path"])
        value["artifacts"] = [self.page(i, source_image_path=item["source_image_path"])
                              for i, item in enumerate(reversed(self.received()), 1)]
        handler = self.handler()
        result = handler.submit(value)
        self.assertTrue(result["accepted"], result)
        self.assertEqual(handler.submit(value), result)
        self.assertEqual([page.asset_id for page in self.checkpoint.pages], [second.asset_id, first.asset_id])
        self.assertEqual(len(self.store.read().assets), 2)

    def test_modified_view_cannot_authorize_an_unreceived_image(self):
        _, source = self.receive("received", (10, 80, 120))
        view = self.directory / "work/generated-assets.json"
        data = json.loads(view.read_text(encoding="utf-8"))
        data["images"][0]["source_image_path"] = str(source.with_name("not-received.png"))
        view.write_text(json.dumps(data), encoding="utf-8")
        result = self.handler().submit(self.payload(self.received()[0]["source_image_path"]))
        self.assertFalse(result["accepted"])
        self.assertEqual(result["received_images"][0]["source_image_path"], str(source))
        self.assertEqual(len(self.store.read().assets), 1)

    def test_production_instructions_show_image_output_and_missing_result_recovery(self):
        prompt = submission_prompt()
        self.assertIn("generatedImage(result)", prompt)
        self.assertIn("结果为空或没有图片路径", prompt)
        self.assertIn("generated-assets.json", prompt)
        self.assertIn("received:only", prompt)
        self.assertNotIn("不需要读取generated-assets.json", prompt)


if __name__ == "__main__":
    unittest.main()
