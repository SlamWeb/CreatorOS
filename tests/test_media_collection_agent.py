"""Account tool -> real loopback HTTP -> isolated SQLite/original-byte ZIP.

ReviewProducer supplies deterministic local files, not model-generated content.
Only fault injection and the local image fixture size use patches; business
services, HTTP authorization, persistence and archive validation remain real.
"""
from dataclasses import replace
from io import BytesIO
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import httpx
from PIL import Image
from pydantic import ValidationError

from creatoros.ai.types import ToolCall
from creatoros.context import RuntimeContext
from creatoros.storage import ContentRepository, ContentRevision, Series, TopicRemoval, TopicSource
from creatoros.tools import execute_tool_call, tools
from creatoros.tools.host_contract import model_tool_schemas
from creatoros.tools.media_collections import ManageMediaCollectionArgs, manage_media_collection
from creatoros.web.chat import ACCOUNT_TOOLS, STUDIO_TOOLS
from creatoros.web.chat_links import tool_links
from tests.agent_studio_support import serve
from tests.studio_review_fixtures import make_fixture
from tests.test_media_collections import business_snapshot


TOOL = "manage_media_collection"


class MediaCollectionAgentSchemaTests(unittest.TestCase):
    def test_action_contract_rejects_irrelevant_missing_and_untrusted_fields(self):
        valid = [
            {"action": "list"}, {"action": "list", "name": "坚持"},
            {"action": "get", "collection_id": "one"},
            {"action": "create", "name": "素材"},
            {"action": "update", "collection_id": "one", "expected_version": 1,
             "changes": {"excluded_ids": []}},
            {"action": "remove", "collection_id": "one", "expected_version": 1},
            {"action": "export", "collection_id": "one"},
            {"action": "export", "collection_id": "one", "expected_version": 1,
             "snapshot_digest": "a" * 64},
        ]
        for args in valid:
            with self.subTest(valid=args):
                ManageMediaCollectionArgs(creator_id="review-lab", **args)
        invalid = [
            {"action": "produce"}, {"action": "get"}, {"action": "create"},
            {"action": "list", "changes": {"name": "misleading"}},
            {"action": "create", "name": "x", "request_id": "model-owned"},
            {"action": "export", "collection_id": "one", "path": "C:/output.zip"},
            {"action": "export", "collection_id": "one", "expected_version": 1},
            {"action": "export", "collection_id": "one", "snapshot_digest": "a" * 64},
            {"action": "update", "collection_id": "one", "expected_version": 1, "changes": {}},
            {"action": "update", "collection_id": "one", "expected_version": "1", "changes": {"name": "x"}},
            {"action": "remove", "collection_id": "one", "expected_version": 0},
            {"action": "list", "limit": 101}, {"action": "list", "offset": -1},
            {"action": "get", "collection_id": "one", "export_id": "../../outside"},
        ]
        for args in invalid:
            with self.subTest(invalid=args), self.assertRaises(ValidationError):
                ManageMediaCollectionArgs(creator_id="review-lab", **args)

    def test_host_schema_hides_account_and_model_cannot_override_it(self):
        self.assertIn(TOOL, ACCOUNT_TOOLS & STUDIO_TOOLS)
        ctx = RuntimeContext(Path.cwd(), creator_id="review-lab", allowed_tools=ACCOUNT_TOOLS)
        schema = next(s["function"] for s in model_tool_schemas(tools, ctx)
                      if s["function"]["name"] == TOOL)
        self.assertNotIn("creator_id", schema["parameters"]["properties"])
        self.assertNotIn("creator_id", schema["parameters"]["required"])
        for raw, error_type in [
            ({"action": "list", "creator_id": "other"}, "agent_scope_rejected"),
            ({"action": "export"}, "invalid_arguments"),
            ({"action": "export", "collection_id": "one", "path": "C:/x.zip"}, "invalid_arguments"),
        ]:
            result = execute_tool_call(ToolCall("invalid", TOOL, json.dumps(raw)), ctx, model_requested=True)
            self.assertTrue(result.is_error)
            self.assertEqual(result.error_type, error_type)
            self.assertEqual(tool_links(TOOL, result.content, is_error=True, error_type=result.error_type), [])


class MediaCollectionAgentHttpTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db, self.runs, self.producer, self.app = make_fixture(self.root)
        self.addCleanup(self.db.close)
        self.first = self.produce("review-1", 2)
        self.extra = self.produce("review-2", 1)
        self.requests = []

        @self.app.middleware("http")
        async def record(request, call_next):
            self.requests.append((request.method, request.url.path))
            return await call_next(request)

        server = serve(self.app)
        self.url = server.__enter__()
        self.addCleanup(server.__exit__, None, None, None)
        self.web = httpx.Client(base_url=self.url, trust_env=False, timeout=30)
        self.addCleanup(self.web.close)
        response = self.web.post("/api/agent/sessions", json={"creator_id": "review-lab"})
        self.assertEqual(response.status_code, 201, response.text)
        self.context = RuntimeContext(self.root, studio_url=self.url, creator_id="review-lab",
            agent_session_id=response.json()["id"], user_request_id="host-owned-turn", allowed_tools=ACCOUNT_TOOLS)
        self.requests.clear()

    def produce(self, topic_id, count):
        self.producer.count = count
        run = self.runs.create(topic_id)
        self.runs.execute(run.id)
        return self.runs.get(run.id)

    def call(self, action, *, error=False, context=None, **arguments):
        result = execute_tool_call(ToolCall("collection-call", TOOL,
            json.dumps({"action": action, **arguments}, ensure_ascii=False)),
            context or self.context, model_requested=True)
        self.assertEqual(result.is_error, error, result.content)
        try:
            data = json.loads(result.model_content or result.content)
        except ValueError:
            self.assertTrue(result.is_error)
            data = {"message": result.content}
        return result, data

    def create(self, **changes):
        _, data = self.call("create", name="素材测试", run_ids=[self.first.id, self.extra.id], **changes)
        return data

    def files(self):
        return {str(path.relative_to(self.root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (self.root / "outputs").rglob("*") if path.is_file()}

    def download(self, receipt):
        return self.web.get(receipt["url"])

    def export(self, collection, *, current=False):
        args = {} if current else {"expected_version": collection["version"],
                                   "snapshot_digest": collection["snapshot_digest"]}
        result, projected = self.call("export", collection_id=collection["id"], **args)
        receipt = json.loads(result.content)
        self.assertTrue(receipt["url"].startswith("/api/creators/"))
        self.assertTrue(receipt["collection_url"])
        self.assertNotIn("url", projected)
        self.assertNotIn("collection_url", projected)
        self.assertNotIn("/api/creators", result.model_content)
        self.assertEqual(set(projected), {"action", "name", "selected_count", "message"})
        self.assertEqual(projected["action"], "export")
        self.assertEqual(projected["name"], receipt["name"])
        self.assertEqual(projected["selected_count"], receipt["selected_count"])
        self.assertIn("按钮", projected["message"])
        self.assertRegex(receipt["export_id"], r"^[a-f0-9]{64}$")
        self.assertRegex(receipt["checksum"], r"^[a-f0-9]{64}$")
        self.assertGreater(receipt["size_bytes"], 0)
        self.assertNotIn(receipt["export_id"], result.model_content)
        self.assertNotIn(receipt["checksum"], result.model_content)
        return receipt

    def test_crud_idempotency_cas_and_old_business_files_unchanged(self):
        before, files, calls = business_snapshot(self.db), self.files(), self.producer.calls
        first = self.create()
        self.assertEqual(self.create()["id"], first["id"])
        _, listed = self.call("list", include_sources=True)
        self.assertEqual([row["id"] for row in listed["items"]], [first["id"]])
        self.assertEqual({row["run_id"] for row in listed["sources"]}, {self.first.id, self.extra.id})
        _, detail = self.call("get", collection_id=first["id"])
        self.assertEqual(detail["snapshot_digest"], first["snapshot_digest"])
        ids = [item["id"] for item in detail["items"]]
        _, saved = self.call("update", collection_id=first["id"], expected_version=first["version"],
            changes={"name": "重排素材", "order_ids": list(reversed(ids)), "excluded_ids": [ids[0]]})
        self.assertEqual([item["id"] for item in saved["items"]], list(reversed(ids)))
        self.assertEqual(saved["selected_count"], 2)
        self.call("update", error=True, collection_id=first["id"], expected_version=first["version"],
                  changes={"name": "lost update"})
        self.call("remove", error=True, collection_id=first["id"], expected_version=first["version"])
        _, removed = self.call("remove", collection_id=first["id"], expected_version=saved["version"])
        self.assertTrue(removed["deleted"])
        self.call("get", error=True, collection_id=first["id"])
        self.assertEqual(business_snapshot(self.db), before)
        self.assertEqual(self.files(), files)
        self.assertEqual(self.producer.calls, calls)
        self.assertIn(("POST", "/api/creators/review-lab/collections"), self.requests)
        self.assertTrue(all("/collections" in path for _, path in self.requests))

    def test_name_filter_matches_casefold_substrings_and_keeps_duplicate_names(self):
        before, files, calls = business_snapshot(self.db), self.files(), self.producer.calls
        created = []
        for number, name in enumerate(("坚持 Scene", "坚持 Scene", "新的坚持 Scene片段", "其他素材")):
            _, collection = self.call("create", name=name, run_ids=[self.first.id],
                context=replace(self.context, user_request_id=f"create-folder-{number}"))
            created.append(collection)
        self.assertNotEqual(created[0]["id"], created[1]["id"])
        self.requests.clear()
        for name in ("坚持 Scene", "坚持 SCENE"):
            _, filtered = self.call("list", name=name)
            self.assertEqual({item["id"] for item in filtered["items"]},
                             {item["id"] for item in created[:3]})
            self.assertEqual(filtered["page"]["total"], 3)
            self.assertEqual(sum(item["name"] == "坚持 Scene" for item in filtered["items"]), 2)
        _, narrow = self.call("list", name="新的坚持")
        self.assertEqual([item["id"] for item in narrow["items"]], [created[2]["id"]])
        _, missing = self.call("list", name="不存在的文件夹")
        self.assertEqual(missing["items"], [])
        self.assertEqual(missing["page"]["total"], 0)
        self.assertEqual(self.requests, [("GET", "/api/creators/review-lab/collections")] * 4)
        self.assertEqual(business_snapshot(self.db), before)
        self.assertEqual(self.files(), files)
        self.assertEqual(self.producer.calls, calls)

    def test_current_and_explicit_exports_replay_frozen_zip_after_reordering(self):
        collection = self.create()
        before, files, calls = business_snapshot(self.db), self.files(), self.producer.calls
        raw_detail = self.web.get(f"/api/creators/review-lab/collections/{collection['id']}").json()
        originals = [self.web.get(item["url"]).content for item in raw_detail["items"]]
        receipt = self.export(collection, current=True)
        response = self.download(receipt)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "application/zip")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(len(response.content), receipt["size_bytes"])
        self.assertEqual(hashlib.sha256(response.content).hexdigest(), receipt["checksum"])
        with ZipFile(BytesIO(response.content)) as archive:
            self.assertEqual(archive.namelist(), ["0001.png", "0002.png", "0003.png", "manifest.json"])
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["snapshot_digest"], collection["snapshot_digest"])
            self.assertEqual([archive.read(f"{i:04d}.png") for i in range(1, 4)], originals)
        self.assertEqual(self.export(collection), receipt)
        query_result, queried = self.call("get", collection_id=collection["id"], export_id=receipt["export_id"])
        raw_query = json.loads(query_result.content)
        self.assertEqual(raw_query["export_id"], receipt["export_id"])
        self.assertEqual(raw_query["checksum"], receipt["checksum"])
        self.assertEqual(raw_query["size_bytes"], receipt["size_bytes"])
        self.assertEqual(set(queried), {"action", "name", "selected_count", "message"})
        self.assertEqual(queried["action"], "get")
        self.assertEqual(queried["name"], receipt["name"])
        self.assertEqual(queried["selected_count"], receipt["selected_count"])
        self.assertNotIn("url", queried)
        self.assertNotIn("collection_url", queried)
        self.assertNotIn("/api/creators", query_result.model_content)
        self.assertEqual(raw_query["url"], receipt["url"])
        _, changed = self.call("update", collection_id=collection["id"], expected_version=collection["version"],
            changes={"name": "新顺序", "order_ids": [i["id"] for i in reversed(collection["items"])]})
        self.assertEqual(self.download(receipt).content, response.content)
        newer = self.export(changed)
        self.assertNotEqual(newer["export_id"], receipt["export_id"])
        with ZipFile(BytesIO(self.download(newer).content)) as archive:
            self.assertEqual([archive.read(f"{i:04d}.png") for i in range(1, 4)], list(reversed(originals)))
        _, selected = self.call("update", collection_id=collection["id"], expected_version=changed["version"],
            changes={"excluded_ids": [changed["items"][1]["id"]]})
        subset = self.export(selected)
        self.assertEqual(subset["selected_count"], 2)
        with ZipFile(BytesIO(self.download(subset).content)) as archive:
            self.assertEqual(archive.namelist(), ["0001.png", "0002.png", "manifest.json"])
            self.assertEqual([archive.read("0001.png"), archive.read("0002.png")], [originals[2], originals[0]])
        self.assertEqual(self.download(receipt).content, response.content)
        result, projected = self.call("export", error=True, collection_id=collection["id"],
            expected_version=collection["version"], snapshot_digest=collection["snapshot_digest"])
        self.assertNotIn("export_id", projected)
        self.assertNotIn("items", projected)
        self.assertEqual(tool_links(TOOL, result.content, is_error=True, error_type=result.error_type), [])
        self.assertEqual(business_snapshot(self.db), before)
        self.assertEqual(self.files(), files)
        self.assertEqual(self.producer.calls, calls)

    def test_pagination_keeps_hundred_images_out_of_model_default_view(self):
        repo = ContentRepository(self.db)
        repo.add_topic(topic_id="hundred", series_id="agent-notes", title="100 local files", source=TopicSource.MANUAL)
        image_new = Image.new
        with patch("tests.studio_review_fixtures.Image.new", side_effect=lambda mode, size, color:
                   image_new(mode, (24, 32), tuple(channel % 256 for channel in color))):
            hundred = self.produce("hundred", 100)
        _, collection = self.call("create", name="100图", run_ids=[hundred.id])
        result, first = self.call("get", collection_id=collection["id"])
        raw = json.loads(result.content)
        self.assertEqual(len(raw["items"]), 100)
        self.assertEqual(len(first["items"]), 20)
        self.assertEqual(first["page"], {"offset": 0, "limit": 20, "total": 100, "next_offset": 20})
        self.assertEqual(first["selected_count"], 100)
        self.assertTrue(all("url" not in item for item in first["items"]))
        _, last = self.call("get", collection_id=collection["id"], offset=90, limit=10)
        self.assertEqual([i["id"] for i in last["items"]], [i["id"] for i in raw["items"][90:]])
        self.assertIsNone(last["page"]["next_offset"])
        _, listing = self.call("list")
        self.assertNotIn("sources", listing)
        self.assertNotIn("items", listing["items"][0])

    def test_cross_account_guard_forged_creator_and_source_transfer_rejected(self):
        collection = self.create()
        receipt = self.export(collection)
        repo = ContentRepository(self.db)
        repo.create_creator(creator_id="other", display_name="其他账号")
        repo.create_series(series_id="other-series", creator_id="other", name="Other", description="",
                           audience="", skill_name="knowledge-to-carousel")
        repo.add_topic(topic_id="other-topic", series_id="other-series", title="Other", source=TopicSource.MANUAL)
        foreign = self.produce("other-topic", 1)
        result = manage_media_collection("list", "other", context=self.context)
        self.assertTrue(result.is_error, result.content)
        request_count = len(self.requests)
        rejected, _ = self.call("get", error=True, collection_id=collection["id"], creator_id="other")
        self.assertEqual(rejected.error_type, "agent_scope_rejected")
        self.assertEqual(len(self.requests), request_count)
        self.call("update", error=True, collection_id=collection["id"], expected_version=collection["version"],
                  changes={"run_ids": [foreign.id]})
        path = receipt["url"].replace("review-lab", "other")
        self.assertEqual(self.web.get(path).status_code, 404)
        with self.db.session() as session:
            session.get(Series, "agent-notes").creator_id = "other"
        self.assertIn(self.download(receipt).status_code, (404, 409))
        self.call("get", error=True, collection_id=collection["id"], export_id=receipt["export_id"])

    def test_frozen_owner_source_removal_and_source_byte_changes_reject_saved_export(self):
        collection = self.create()
        receipt = self.export(collection)
        revision_id = collection["items"][0]["revision_id"]
        with self.db.session() as session:
            revision = session.get(ContentRevision, revision_id)
            original_input = dict(revision.production_input_json)
            revision.production_input_json = {**original_input, "creator_id": "forged-owner"}
            image = Path(revision.artifact_directory) / "images/01.png"
        self.assertIn(self.download(receipt).status_code, (404, 409))
        with self.db.session() as session:
            session.get(ContentRevision, revision_id).production_input_json = original_input
        original = image.read_bytes()
        image.write_bytes(original + b"changed")
        self.assertIn(self.download(receipt).status_code, (404, 409))
        image.write_bytes(original)
        self.assertEqual(self.download(receipt).status_code, 200)
        _, saved = self.call("update", collection_id=collection["id"], expected_version=collection["version"],
                             changes={"run_ids": [self.extra.id]})
        self.assertIn(self.download(receipt).status_code, (404, 409))
        self.call("update", collection_id=collection["id"], expected_version=saved["version"],
                  changes={"run_ids": [self.first.id, self.extra.id]})
        self.assertEqual(self.download(receipt).status_code, 200)
        with self.db.session() as session:
            session.add(TopicRemoval(topic_id="review-1", series_id="agent-notes", creator_id="review-lab",
                                     request_id="removed-fixture", status="archived"))
        self.assertIn(self.download(receipt).status_code, (404, 409))

    def test_corrupt_export_is_rejected_and_replay_never_replaces_it(self):
        collection = self.create()
        receipt = self.export(collection)
        archive = self.app.state.media_collections.exports.root / receipt["export_id"] / "images.zip"
        original = archive.read_bytes()
        archive.write_bytes(b"broken-zip")
        self.assertIn(self.download(receipt).status_code, (404, 409))
        result, projected = self.call("get", error=True, collection_id=collection["id"], export_id=receipt["export_id"])
        self.assertFalse(projected.get("url"))
        self.assertNotIn("export_id", projected)
        self.call("export", error=True, collection_id=collection["id"], expected_version=collection["version"],
                  snapshot_digest=collection["snapshot_digest"])
        self.assertEqual(archive.read_bytes(), b"broken-zip")
        archive.write_bytes(original)
        self.assertEqual(self.download(receipt).status_code, 200)
        _, removed = self.call("remove", collection_id=collection["id"], expected_version=collection["version"])
        self.assertTrue(removed["deleted"])
        self.assertEqual(self.download(receipt).status_code, 404)

    def test_receipt_download_links_accept_only_exact_successful_export_route(self):
        collection = self.create()
        receipt = self.export(collection)
        links = tool_links(TOOL, json.dumps(receipt), studio_url=self.url)
        downloads = [link for link in links if link.get("kind") == "download"]
        self.assertEqual([link["url"] for link in downloads], [receipt["url"]])
        self.assertEqual(self.web.get(downloads[0]["url"]).status_code, 200)
        for poisoned in [
            "https://evil.example" + receipt["url"], "//evil.example" + receipt["url"],
            receipt["url"] + "?path=C:/secret", receipt["url"] + "#fragment",
            receipt["url"].replace("review-lab", "other"),
            receipt["url"].replace(receipt["export_id"], "0" * 64),
            receipt["url"].replace("/download", "/%64ownload"),
            receipt["url"].replace("/download", "\\download"),
            "file:///C:/secret.zip", "javascript:alert(1)",
        ]:
            with self.subTest(url=poisoned):
                bad = {**receipt, "url": poisoned}
                rejected = tool_links(TOOL, json.dumps(bad), studio_url=self.url)
                self.assertFalse(any(link.get("kind") == "download" for link in rejected))
        self.assertEqual(tool_links(TOOL, json.dumps(receipt), is_error=True), [])
        self.assertEqual(tool_links("untrusted_tool", json.dumps(receipt)), [])


if __name__ == "__main__":
    unittest.main()
