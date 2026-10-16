"""Local text transformations only; no publishing or network calls."""
import unittest
from datetime import datetime, timedelta, timezone

from creatoros.publication import PublicationService


class PublicationMatchingTests(unittest.TestCase):
    def setUp(self):
        self.started = datetime.now(timezone.utc)
        self.payload = {"account_id": "owner", "title": "四个英语词", "content": "正文第一段。\n第二段。",
                        "tags": ["英语词汇", "英语学习"], "image_count": 1}
        self.note = {"id": "note", "url": "https://www.xiaohongshu.com/explore/note",
                     "author_id": "owner", "title": self.payload["title"], "images": ["image"],
                     "body": "正文第一段。\n\t\n第二段。\n#英语词汇[话题]# #英语学习打卡[话题]#",
                     "published_at": self.started.isoformat()}

    def matches(self, **changes):
        return PublicationService._matches({**self.note, **changes}, self.payload, self.started, False)

    def test_platform_topic_alias_preserves_exact_prose(self):
        self.assertTrue(self.matches())
        evidence = PublicationService._body_match(self.note, self.payload)
        self.assertEqual(evidence["platform_topics"], ["英语词汇", "英语学习打卡"])
        self.assertTrue(evidence["topic_labels_changed"])

    def test_original_and_plain_topics_and_no_suffix(self):
        for suffix in ("", "\n#英语词汇 #英语学习", "\n#英语词汇[话题]# #英语学习[话题]#"):
            with self.subTest(suffix=suffix):
                self.assertTrue(self.matches(body=self.payload["content"] + suffix))

    def test_modified_prose_inline_markers_and_wrong_topic_count_rejected(self):
        for body in (self.note["body"].replace("第二段", "错误内容"),
                     "正文第一段。#英语词汇[话题]# 第二段。#英语学习[话题]#",
                     self.note["body"] + " #额外话题[话题]#",
                     self.payload["content"] + " #英语学习打卡[话题]#",
                     self.payload["content"] + " #英语词汇 #英语学习打卡",
                     self.note["body"] + "额外正文"):
            with self.subTest(body=body):
                self.assertFalse(self.matches(body=body))

    def test_literal_hashtags_in_prose_are_not_stripped(self):
        self.payload["content"] = "正文包含 #英语学习，这不是末尾标签。"
        self.assertTrue(self.matches(body=self.payload["content"] + "\n#英语词汇[话题]# #英语学习打卡[话题]#"))
        self.assertFalse(self.matches(body="正文包含 ，这不是末尾标签。\n#英语词汇[话题]# #英语学习打卡[话题]#"))

    def test_identity_title_image_count_and_time_remain_required(self):
        for changes in ({"author_id": "other"}, {"title": "别的标题"}, {"images": []},
                        {"published_at": None}, {"published_at": (self.started - timedelta(hours=1)).isoformat()}):
            with self.subTest(changes=changes):
                self.assertFalse(self.matches(**changes))


if __name__ == "__main__":
    unittest.main()
