"""Local parser/security checks; never send an actual publication."""
import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from creatoros.publication.mcp_client import XhsMcpClient, XhsMcpError, normalize_note


ACCOUNT = "63bfc5fe0000000027028b34"
NOTE = "6ac8ee30000000001802f874"


class StubClient(XhsMcpClient):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.calls = []
        self.responses = {}

    async def _call(self, name, arguments):
        self.calls.append((name, arguments))
        return self.responses[name]


def profile():
    return {"userBasicInfo": {"nickname": "测试账号"}, "feeds": [{
        "id": NOTE, "xsecToken": "private-note-token",
        "noteCard": {"displayTitle": "标题", "user": {"userId": ACCOUNT}},
    }]}


def note():
    return {"note": {"noteId": NOTE, "user": {"userId": ACCOUNT},
                     "title": "标题", "desc": "正文", "time": 1_790_000_000_000,
                     "imageList": [{"urlDefault": "https://example.com/image.jpg"}],
                     "interactInfo": {"likedCount": "4", "commentCount": "", "sharedCount": "1.2万"},
                     "xsecToken": "private-note-token"}}


class PublicationMcpTests(unittest.IsolatedAsyncioTestCase):
    def test_normalization_does_not_invent_counts_or_expose_tokens(self):
        result = normalize_note(note())
        self.assertEqual(result["metrics"]["likes"], 4)
        self.assertIsNone(result["metrics"]["views"])
        self.assertIsNone(result["metrics"]["comments"])
        self.assertIsNone(result["metrics"]["shares"])
        self.assertNotIn("private-note-token", str(result))
        self.assertTrue(result["published_at"].endswith("+00:00"))

    async def test_identity_uses_own_author_id_not_nickname(self):
        client = StubClient()
        client.responses["get_my_profile"] = profile()
        self.assertEqual((await client.identity())["id"], ACCOUNT)
        client.responses["get_my_profile"]["feeds"] = []
        with self.assertRaises(XhsMcpError):
            await client.identity()

    async def test_inspection_reacquires_private_token_and_returns_canonical_url(self):
        client = StubClient()
        client.responses = {"get_my_profile": profile(), "get_feed_detail": note()}
        result = await client.inspect(f"https://www.xiaohongshu.com/explore/{NOTE}")
        self.assertEqual(result["id"], NOTE)
        self.assertEqual(client.calls[-1][1]["xsec_token"], "private-note-token")
        self.assertNotIn("private-note-token", str(result))

    async def test_untrusted_urls_rejected_before_transport(self):
        client = StubClient()
        for url in ["http://127.0.0.1/secrets", "https://xiaohongshu.com.evil.com/explore/" + NOTE,
                    "https://www.xiaohongshu.com/user/profile/" + ACCOUNT,
                    "https://user:password@www.xiaohongshu.com/explore/" + NOTE]:
            with self.assertRaises(XhsMcpError):
                await client.inspect(url)
        self.assertEqual(client.calls, [])

    async def test_readback_candidates_are_not_title_only_receipts(self):
        client = StubClient()
        client.responses = {"get_my_profile": profile(), "get_feed_detail": note()}
        candidates = await client.find_candidates({"title": "标题"}, datetime.now(timezone.utc))
        self.assertEqual(candidates[0]["body"], "正文")
        self.assertEqual(candidates[0]["image_count"], 1)
        self.assertTrue(candidates[0]["published_at"])

    async def test_simulated_publish_checks_account_hash_and_container_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "source.png"
            Image.new("RGB", (4, 4), "white").save(image)
            digest = hashlib.sha256(image.read_bytes()).hexdigest()
            client = StubClient(staging_root=root / "staging")
            client.responses = {"get_my_profile": profile(), "publish_content": {"verification_required": True}}
            payload = {"account_id": ACCOUNT, "image_hashes": [digest], "title": "标题", "content": "正文", "visibility": "仅自己可见"}
            result = await client.publish(payload, "test-publication", [image])
            self.assertTrue(result["verification_required"])
            self.assertEqual(client.calls[-1][1]["images"], ["/app/data/publications/test-publication/01.png"])
            self.assertEqual(hashlib.sha256((root / "staging/test-publication/01.png").read_bytes()).hexdigest(), digest)
            client.calls.clear()
            payload["account_id"] = "another-account"
            with self.assertRaises(XhsMcpError):
                await client.publish(payload, "test-publication", [image])
            self.assertNotIn("publish_content", [name for name, _ in client.calls])
            payload["account_id"] = ACCOUNT
            payload["image_hashes"] = ["0" * 64]
            with self.assertRaises(XhsMcpError):
                await client.publish(payload, "test-publication", [image])
            with self.assertRaises(XhsMcpError):
                client._stage("../escape", [image], [digest])


if __name__ == "__main__":
    unittest.main()
