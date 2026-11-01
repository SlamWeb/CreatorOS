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
        self.assertNotIn("不需要读取generated-assets.json", prompt)


if __name__ == "__main__":
    unittest.main()
