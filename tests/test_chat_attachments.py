"""Local image validation/serialization tests; no model or generated-content claims."""
import base64
from copy import deepcopy
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from fastapi import HTTPException
from PIL import Image

from creatoros.ai.deepseek import DeepSeekProvider
from creatoros.ai.context import estimate_tokens, project_image_history
from creatoros.ai.context import ModelContext
from creatoros.agent.compaction import CompactionPlan
from creatoros.agent.compaction_summary import serialize_messages_for_summary
from creatoros.web.chat_attachments import ChatAttachments, MAX_ENCODED


def image_bytes(kind="PNG", color="red", size=(24, 32), animated=False):
    stream = io.BytesIO()
    image = Image.new("RGB", size, color)
    if animated:
        image.save(stream, format=kind, save_all=True,
                   append_images=[Image.new("RGB", size, "blue")], duration=100)
    else:
        image.save(stream, format=kind)
    return stream.getvalue()


class ChatAttachmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = ChatAttachments(self.root / "chats")
        self.sid = str(uuid4())

    def put(self, data=None, name="reference.png"):
        return self.store.put(self.sid, name, base64.b64encode(data or image_bytes()).decode())

    def denied(self, code, call):
        with self.assertRaises(HTTPException) as caught:
            call()
        self.assertEqual(caught.exception.status_code, code)

    def test_roundtrip_duplicate_and_restart(self):
        meta = self.put(name="../nested/reference.png")
        self.assertEqual(meta["name"], "reference.png")
        self.assertEqual((meta["width"], meta["height"]), (24, 32))
        self.assertEqual(self.put(name="renamed.png"), meta)
        restored = ChatAttachments(self.store.root)
        self.assertEqual(restored.get(self.sid, meta["id"]), meta)
        self.assertEqual(restored.path(self.sid, meta["id"]).read_bytes(), image_bytes())
        self.assertNotIn("base64", json.dumps(meta))

    def test_supported_formats_and_static_gif_conversion(self):
        for kind, mime in (("PNG", "image/png"), ("JPEG", "image/jpeg"),
                           ("WEBP", "image/webp"), ("GIF", "image/png")):
            with self.subTest(kind=kind):
                meta = self.put(image_bytes(kind), name="misleading.txt")
                self.assertEqual(meta["mime_type"], mime)
                data = self.store.path(self.sid, meta["id"]).read_bytes()
                if kind == "GIF":
                    self.assertTrue(data.startswith(b"\x89PNG"))

    def test_invalid_payloads_and_formats(self):
        for encoded in ("%%bad%%", "", base64.b64encode(b"not an image").decode(),
                        base64.b64encode(image_bytes("BMP")).decode(),
                        base64.b64encode(image_bytes("GIF", animated=True)).decode()):
            self.denied(422, lambda: self.store.put(self.sid, "x.png", encoded))
        self.denied(413, lambda: self.store.put(self.sid, "x.png", "a" * (MAX_ENCODED + 1)))

    def test_dimensions_rejected(self):
        self.denied(422, lambda: self.put(image_bytes(size=(8193, 1))))

    def test_truncated_jpeg_requires_pixel_decode(self):
        raw = image_bytes("JPEG", size=(100, 100))
        for cut in (2, 10, 30, 50):
            with self.subTest(cut=cut):
                self.denied(422, lambda: self.put(raw[:-cut], "broken.jpg"))

    def test_cross_session_and_reference_url_rejected(self):
        meta = self.put()
        other = str(uuid4())
        self.denied(404, lambda: self.store.get(other, meta["id"]))
        forged = {**meta, "url": meta["url"].replace(self.sid, other)}
        self.denied(403, lambda: self.store.resolve(self.sid, forged))
        for sid, aid in (("../escape", meta["id"]), (self.sid, "../escape")):
            self.denied(404, lambda: self.store.get(sid, aid))

    def test_digest_and_metadata_tampering_rejected(self):
        meta = self.put()
        folder = self.store.path(self.sid, meta["id"]).parent
        (folder / "image").write_bytes(image_bytes(color="blue"))
        self.denied(404, lambda: self.store.get(self.sid, meta["id"]))
        (folder / "image").write_bytes(image_bytes())
        (folder / "meta.json").write_text(json.dumps({**meta, "id": "0" * 64}), encoding="utf-8")
        self.denied(404, lambda: self.store.get(self.sid, meta["id"]))

    def test_metadata_types_and_actual_image_agree(self):
        meta = self.put()
        metadata = self.store.path(self.sid, meta["id"]).parent / "meta.json"
        for changed in ({"size": "oops"}, {"size": True}, {"size": -1}, {"size": meta["size"] + 1},
                        {"name": []}, {"name": ""}, {"name": "x" * 161},
                        {"width": -1}, {"width": True}, {"width": 25}, {"height": "32"},
                        {"mime_type": "image/jpeg"}):
            with self.subTest(changed=changed):
                metadata.write_text(json.dumps({**meta, **changed}), encoding="utf-8")
                self.denied(404, lambda: self.store.get(self.sid, meta["id"]))
        metadata.write_text(json.dumps(meta), encoding="utf-8")
        self.assertEqual(self.store.get(self.sid, meta["id"]), meta)

    def test_reupload_repairs_same_hash_and_existing_reference(self):
        meta = self.put()
        path = self.store.path(self.sid, meta["id"])
        for corruption in ("missing", "bytes", "metadata"):
            with self.subTest(corruption=corruption):
                if corruption == "missing":
                    path.unlink()
                elif corruption == "bytes":
                    path.write_bytes(b"truncated")
                else:
                    (path.parent / "meta.json").write_text("{broken", encoding="utf-8")
                self.denied(404, lambda: self.store.resolve(self.sid, meta))
                repaired = self.put()
                self.assertEqual(repaired["id"], meta["id"])
                encoded = self.store.resolve(self.sid, meta).split(",", 1)[1]
                self.assertEqual(base64.b64decode(encoded), image_bytes())

    def test_windows_junction_inside_root_cannot_cross_sessions(self):
        try:
            import _winapi
        except ImportError:
            self.skipTest("Windows junction regression")
        meta = self.put()
        other = str(uuid4())
        _winapi.CreateJunction(str(self.store.root / self.sid), str(self.store.root / other))
        self.denied(404, lambda: self.store.get(other, meta["id"]))
        self.denied(404, lambda: self.store.put(other, "forged.png", base64.b64encode(image_bytes()).decode()))

    def test_symlink_image_and_session_rejected(self):
        meta = self.put()
        path = self.store.path(self.sid, meta["id"])
        outside = self.root / "outside.png"
        outside.write_bytes(image_bytes())
        path.unlink()
        try:
            path.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"Host does not allow symlink creation: {exc}")
        self.denied(404, lambda: self.store.get(self.sid, meta["id"]))
        other = str(uuid4())
        (self.store.root / other).symlink_to(self.store.root / self.sid, target_is_directory=True)
        self.denied(404, lambda: self.store.get(other, meta["id"]))

    def test_provider_materializes_only_request_not_ledger(self):
        meta = self.put()
        ledger = [{"role": "user", "creatoros_source": "tool_images", "content": [{"type": "text", "text": "看图"},
                   {"type": "image_ref", "attachment": meta}]}]
        before = deepcopy(ledger)
        provider = DeepSeekProvider.__new__(DeepSeekProvider)
        provider.image_resolver = lambda ref: self.store.resolve(self.sid, ref)
        request = provider._to_openai_messages(ledger)
        self.assertEqual(ledger, before)
        self.assertNotIn("creatoros_source", request[0])
        image = next(block for block in request[0]["content"] if block["type"] == "image_url")
        encoded = image["image_url"]["url"]
        self.assertTrue(encoded.startswith("data:image/png;base64,"))
        self.assertEqual(base64.b64decode(encoded.split(",", 1)[1]), image_bytes())
        self.assertNotIn("base64", json.dumps(ledger))

    def test_provider_requires_resolver_and_user_role(self):
        meta = self.put()
        provider = DeepSeekProvider.__new__(DeepSeekProvider)
        provider.image_resolver = None
        message = {"role": "user", "content": [{"type": "image_ref", "attachment": meta}]}
        with self.assertRaises(ValueError):
            provider._to_openai_messages([message])
        provider.image_resolver = lambda ref: self.store.resolve(self.sid, ref)
        with self.assertRaises(ValueError):
            provider._to_openai_messages([{**message, "role": "tool"}])

    def test_image_budget_keeps_recent_refs_and_never_mutates_ledger(self):
        meta = self.put()
        messages = [{"role": "user", "content": [{"type": "image_ref", "attachment": {
            **meta, "id": str(index), "size": 4 * 1024 * 1024}}]} for index in range(8)]
        before = deepcopy(messages)
        projected = project_image_history(messages)
        self.assertEqual(messages, before)
        self.assertEqual([m["content"][0]["type"] for m in projected], ["text"] * 2 + ["image_ref"] * 6)
        self.assertIn("read_image", projected[0]["content"][0]["text"])
        self.assertGreater(estimate_tokens(messages), estimate_tokens([]) + 8 * 1536)

    def test_host_image_does_not_become_a_new_user_turn_during_compaction(self):
        meta = self.put()
        messages = [
            {"role": "user", "content": "只比较，不要修改 Skill"},
            {"role": "assistant", "tool_calls": [{"id": "read", "name": "read_image", "arguments": "{}"}]},
            {"role": "tool", "tool_call_id": "read", "content": "x" * 5000},
            {"role": "user", "creatoros_source": "tool_images", "content": [
                {"type": "image_ref", "attachment": meta}]},
        ]
        before = deepcopy(messages)
        plan = CompactionPlan.from_context(ModelContext.from_messages(messages, []),
                                           input_limit=10000, keep_recent_tokens=2000)
        self.assertEqual(plan.first_retained_index, 3)
        self.assertEqual(plan.pinned_user_index, 0)
        self.assertEqual(plan.retained_messages[0], messages[-1])
        self.assertEqual(messages, before)
        summary_input, _ = serialize_messages_for_summary([messages[-1]])
        self.assertIn("Host tool images (not a new user request)", summary_input)
        self.assertNotIn("[User]", summary_input)

    def test_compaction_host_images_follow_whole_tool_batches(self):
        meta = self.put()
        messages = [{"role": "user", "content": "比较这几张图，不修改"}]
        for index in range(3):
            messages.extend([
                {"role": "assistant", "tool_calls": [
                    {"id": f"read-{index}", "name": "read_image", "arguments": "{}"},
                    {"id": f"list-{index}", "name": "list_creators", "arguments": "{}"}]},
                {"role": "tool", "tool_call_id": f"read-{index}", "content": "x" * 3000},
                {"role": "tool", "tool_call_id": f"list-{index}", "content": "y" * 3000},
                {"role": "user", "creatoros_source": "tool_images", "content": [
                    {"type": "image_ref", "attachment": meta}]},
            ])
        plan = CompactionPlan.from_context(ModelContext.from_messages(messages, []),
                                           input_limit=20000, keep_recent_tokens=2000)
        self.assertEqual(plan.pinned_user_index, 0)
        self.assertEqual(plan.retained_messages[0]["creatoros_source"], "tool_images")
        cut = plan.first_retained_index
        self.assertEqual(messages[cut - 1]["role"], "tool")
        self.assertEqual(messages[cut - 2]["role"], "tool")


if __name__ == "__main__":
    unittest.main()
