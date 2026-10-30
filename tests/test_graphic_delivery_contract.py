"""Graphical posts: real local media, injected SDK output, no inference."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from creatoros.content import SocialContentPack
from creatoros.integrations.codex import CodexProducerError
from creatoros.integrations.native_image_assets import ImageAssetStore
from creatoros.integrations.native_production import load_checkpoint
from tests import test_structured_handoff as harness


class GraphicDeliveryContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = harness.StructuredHandoffTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.change = lambda value: None
        original = harness.StructuredTurn.stream
        owner = self

        async def stream(turn):
            async for event in original(turn):
                item = getattr(getattr(event, "payload", None), "item", None)
                item = getattr(item, "root", item)
                if getattr(item, "type", None) == "agentMessage" and turn.call["phase"] != "composition":
                    value = json.loads(item.text)
                    directory = Path(turn.call["controls"]["cwd"]).parent
                    cp = load_checkpoint(directory)
                    store = ImageAssetStore(directory, owner.fixture.generated, cp.thread_id, cp.input_digest)
                    for page in value["artifacts"]:
                        asset, _ = store.resolve(page.pop("asset_id"))
                        page["source_image_path"] = asset.source_image_path
                    owner.change(value)
                    item.text = json.dumps(value, ensure_ascii=False)
                yield event

        self.addCleanup(patch.stopall)
        patch.object(harness.StructuredTurn, "stream", stream).start()

    def test_chinese_caption_markdown_and_page_text_round_trip(self):
        def change(value):
            value.update(title="怕失败，也想试一次", text="第一段：保留标点。\n\n第二段：猫 & dog，100%。", hashtags=["#学习", "#图文"])
            value["artifacts"][0]["content"] = "## 第 1 页\n他说：‘试试看。’\nEnglish / 中文 🎨"
        self.change = change
        produced = self.fixture.produce()
        pack = SocialContentPack.load(produced.directory)
        self.assertEqual(pack.publish_copy.title, "怕失败，也想试一次")
        self.assertEqual(pack.publish_copy.body, "第一段：保留标点。\n\n第二段：猫 & dog，100%。")
        self.assertEqual(pack.publish_copy.hashtags, ["#学习", "#图文"])
        self.assertEqual(pack.cards[0].body, "## 第 1 页\n他说：‘试试看。’\nEnglish / 中文 🎨")

    def test_image_only_page_can_have_empty_per_page_body(self):
        self.change = lambda value: value["artifacts"][0].update(content="")
        produced = self.fixture.produce()
        self.assertIsNone(SocialContentPack.load(produced.directory).cards[0].body)

    def test_long_caption_not_truncated(self):
        caption = "每一张图都值得认真交付。" * 800
        self.change = lambda value: value.update(text=caption)
        produced = self.fixture.produce()
        self.assertEqual(SocialContentPack.load(produced.directory).publish_copy.body, caption)

    def test_two_images_keep_their_own_page_text_when_reordered(self):
        self.fixture.transport.poison_index = True  # two distinct SDK image events
        def change(value):
            assets = self.fixture.inventory(self.fixture.directory())
            first = dict(value["artifacts"][0])
            second = dict(first)
            first.update(order=1, source_image_path=assets[1].source_image_path, content="第二张生成，第一张展示")
            second.update(order=2, source_image_path=assets[0].source_image_path, content="第一张生成，第二张展示")
            value["artifacts"] = [first, second]
        self.change = change
        produced = self.fixture.produce()
        pack = SocialContentPack.load(produced.directory)
        self.assertEqual([card.body for card in pack.cards], ["第二张生成，第一张展示", "第一张生成，第二张展示"])
        cp = load_checkpoint(produced.directory)
        self.assertEqual([p.asset_id for p in cp.pages], [a.asset_id for a in reversed(self.fixture.inventory(produced.directory))])

    def test_invalid_complete_does_not_publish_pack(self):
        self.change = lambda value: value.update(complete=False)
        with self.assertRaises(CodexProducerError):
            self.fixture.produce()
        self.assertFalse((self.fixture.directory() / "social_content_pack.json").exists())
        self.assertEqual(self.fixture.transport.generated_count, 1)

    def test_missing_publication_body_rejected(self):
        self.change = lambda value: value.pop("text")
        with self.assertRaises(CodexProducerError):
            self.fixture.produce()
        self.assertFalse((self.fixture.directory() / "social_content_pack.json").exists())

    def test_invalid_page_order_rejected(self):
        self.change = lambda value: value["artifacts"][0].update(order=2)
        with self.assertRaises(CodexProducerError):
            self.fixture.produce()
        self.assertFalse((self.fixture.directory() / "social_content_pack.json").exists())


if __name__ == "__main__":
    unittest.main()
