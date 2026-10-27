"""Isolated focus contracts using real SQLite, files, HTTP and image conversion.

The reused ControlledCalibrationProducer writes synthetic, validated local
artifacts; the recording provider is a controlled protocol harness. Neither is
DeepSeek/Codex quality evidence. No network model or formal service is called.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
import httpx
from PIL import Image
from pydantic import ValidationError

from creatoros.agent.loop import build_model_context
from creatoros.ai.deepseek import DeepSeekProvider
from creatoros.ai.types import StreamEnd, TextDelta
from creatoros.context import RuntimeContext
from creatoros.integrations.producer_skills import ProducerSkillCatalog, SkillInstallService, skills_root_for
from creatoros.runs import ContentRunService
from creatoros.runs.artifacts import validate_artifact
from creatoros.storage import (ContentAttempt, ContentRepository, ContentRevision, ContentRun,
                              ContentRunStatus, Creator, Database, Series, Topic, TopicRemoval,
                              TopicSource, TopicStatus, upgrade_database)
from creatoros.tools import tools
from creatoros.tools.host_contract import model_tool_schemas
from creatoros.web.app import create_app
from creatoros.web.chat import ACCOUNT_TOOLS
from creatoros.web.chat_focus import ChatContextTarget, _read_managed
from tests.agent_studio_support import serve
from tests.skill_calibration_fixture import ControlledCalibrationProducer, protected_records, tree_hashes


class RecordingProtocolProvider:
    """Controlled response only; captures the real Loop's outbound ModelContext."""

    def __init__(self, captures):
        self.client = SimpleNamespace(close=lambda: None)
        self.captures = captures

    def stream(self, context):
        self.captures.append(deepcopy(context.to_request()))
        yield TextDelta("受控传输验证完成。")
        yield StreamEnd("stop")


class ChatFocusTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="creatoros-focus-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        url = f"sqlite:///{(self.root / 'focus.db').as_posix()}"
        upgrade_database(url)
        self.db = Database(url)
        self.addCleanup(self.db.close)
        self.catalog = ProducerSkillCatalog(skills_root_for(self.db))
        self.repo = ContentRepository(self.db)
        self.skill_ids = {}
        for owner, color in (("account-a", "#f1d9a2"), ("account-b", "#adddf1")):
            source = self.root / (owner + "-source")
            (source / "assets").mkdir(parents=True)
            (source / "SKILL.md").write_text(
                f"---\nname: {owner}-skill\ndescription: Isolated focus fixture\n"
                "creatoros-output: social-content-pack.image-carousel\n---\n\n"
                f"FROZEN_{owner}: use assets/reference.png.\n", encoding="utf-8")
            Image.new("RGB", (24, 32), color).save(source / "assets/reference.png")
            self.skill_ids[owner] = self.catalog.register_local(source, role="legacy_end_to_end")["id"]
            self.repo.create_creator(creator_id=owner, display_name="同名账号")
            self.repo.create_series(series_id=owner + "-series", creator_id=owner, name="同名栏目",
                description="", audience="", skill_name=self.skill_ids[owner])
        for topic_id, series_id, brief in (
                ("topic-a", "account-a-series", "SELECTED_TOPIC_A"),
                ("topic-a-other", "account-a-series", "UNSELECTED_SAME_NAME_TOPIC"),
                ("topic-foreign", "account-b-series", "FOREIGN_PRIVATE_TOPIC")):
            self.repo.add_topic(topic_id=topic_id, series_id=series_id, title="完全同名的作品",
                                brief=brief, source=TopicSource.MANUAL)
        self.runs = ContentRunService(self.db, output_root=self.root / "outputs")
        self.captures = []
        self.app = create_app(database=self.db, run_service=self.runs,
            chat_root=self.root / "chats", skill_install_service=SkillInstallService(self.catalog),
            chat_provider_factory=lambda: RecordingProtocolProvider(self.captures),
            eval_root=self.root / "eval", defer_background_start=True)
        self.chat = self.app.state.chat
        self.addCleanup(self.chat.shutdown)
        self.builder = self.chat.focus_context_builder
        self.sid = self.chat.create("account-a")["id"]
        self.run_id, self.revision_id = self.seed_native("topic-a")
        self.other_run_id, self.other_revision_id = self.seed_native("topic-a-other")
        self.foreign_run_id, self.foreign_revision_id = self.seed_native("topic-foreign")
        self.target = dict(series_id="account-a-series", topic_id="topic-a",
                           run_id=self.run_id, revision_id=self.revision_id)

    def seed_native(self, topic_id):
        """Reuse the local native fixture; never invokes a worker or a model."""
        run = self.runs.create(topic_id)
        revision = self.runs.get_active_revision(run.id)
        snapshot = run.input_snapshot_json
        directory = (self.runs.output_root / snapshot["creator_id"] / snapshot["series_id"] /
                     run.id / "revision-001" / "attempt-001")
        ControlledCalibrationProducer().produce_to(directory=directory, pack_id=run.id,
            creator_id=snapshot["creator_id"], series_id=snapshot["series_id"], topic_id=topic_id,
            topic_title=snapshot["topic_title"], skill_directory=Path(snapshot["skill_path"]),
            skill_digest=snapshot["skill_digest"], on_thread_started=lambda _: None,
            cancel_event=Event())
        checked = validate_artifact(directory, production_protocol="native-v1")
        with self.db.session() as session:
            saved = session.get(ContentRun, run.id)
            saved.status = ContentRunStatus.AWAITING_APPROVAL
            session.get(Topic, topic_id).status = TopicStatus.READY
            record = session.get(ContentRevision, revision.id)
            record.artifact_directory = str(directory)
            record.manifest_path = str(directory / "social_content_pack.json")
            record.artifact_digest = checked.artifact_digest
            record.validation_json = checked.model_dump(mode="json")
            session.add(ContentAttempt(id=str(uuid4()), revision_id=revision.id, attempt_number=1,
                status="succeeded", output_directory=str(directory),
                usage_json={"source": "controlled-local-fixture", "model_calls": 0}))
        return run.id, revision.id

    def build(self, target=None, **kwargs):
        return self.builder.build("account-a", self.sid, target or self.target, **kwargs)

    def assert_rejected(self, target=None, creator_id="account-a"):
        with self.assertRaises(HTTPException) as error:
            self.builder.validate(creator_id, target or self.target)
        self.assertIn(error.exception.status_code, (403, 404))

    def test_strict_target_rejects_extra_path_and_revision_without_run(self):
        for target in ({**self.target, "path": "D:/outside"},
                       {"series_id": "../outside", "topic_id": "topic-a"},
                       {"series_id": "account-a-series", "topic_id": "topic-a", "revision_id": self.revision_id}):
            with self.subTest(target=target), self.assertRaises(ValidationError):
                ChatContextTarget.model_validate(target)

    def test_same_name_targets_do_not_search_or_mix_accounts(self):
        bundle = self.build()
        serialized = json.dumps(bundle["data"], ensure_ascii=False)
        self.assertEqual(bundle["data"]["target"], self.target)
        self.assertIn("SELECTED_TOPIC_A", serialized)
        self.assertNotIn("UNSELECTED_SAME_NAME_TOPIC", serialized)
        self.assertNotIn("FOREIGN_PRIVATE_TOPIC", serialized)
        self.assertNotIn("FROZEN_account-b", serialized)
        self.assert_rejected(creator_id="account-b")
        self.assert_rejected({**self.target, "run_id": self.foreign_run_id})
        self.assert_rejected({**self.target, "run_id": self.other_run_id})
        self.assert_rejected({**self.target, "revision_id": self.other_revision_id})

    def test_disabled_creator_series_and_removed_topic_are_rejected(self):
        for model, identifier in ((Creator, "account-a"), (Series, "account-a-series")):
            with self.subTest(model=model.__name__):
                with self.db.session() as session:
                    session.get(model, identifier).is_active = False
                self.assert_rejected()
                with self.db.session() as session:
                    session.get(model, identifier).is_active = True
        with self.db.session() as session:
            session.add(TopicRemoval(topic_id="topic-a", series_id="account-a-series", creator_id="account-a",
                                     status="archived", request_id=uuid4().hex))
        self.assert_rejected()

    def test_frozen_run_and_revision_identity_are_checked(self):
        for model, identifier, field in ((ContentRun, self.run_id, "input_snapshot_json"),
                                         (ContentRevision, self.revision_id, "production_input_json")):
            with self.subTest(model=model.__name__):
                with self.db.session() as session:
                    row = session.get(model, identifier)
                    original = deepcopy(getattr(row, field))
                    setattr(row, field, {**original, "creator_id": "account-b"})
                self.assert_rejected()
                with self.db.session() as session:
                    setattr(session.get(model, identifier), field, original)

    def test_host_resolves_only_selected_topics_latest_run_and_revision(self):
        result = self.build({"series_id": "account-a-series", "topic_id": "topic-a"})
        self.assertEqual(result["data"]["target"], self.target)
        with self.db.session() as session:
            run = session.get(ContentRun, self.run_id)
            run.input_snapshot_json = {**run.input_snapshot_json, "creator_id": "account-b"}
        with self.assertRaises(HTTPException):
            self.build({"series_id": "account-a-series", "topic_id": "topic-a"})

    def test_one_bundle_contains_distinct_current_frozen_text_and_actual_images(self):
        path = self.catalog.locate(self.skill_ids["account-a"]) / "SKILL.md"
        path.write_text(path.read_text(encoding="utf-8").replace("FROZEN_account-a", "CURRENT_account-a"), encoding="utf-8")
        before_records, before_files = protected_records(self.db), tree_hashes(self.runs.output_root)
        bundle = self.build()
        self.assertIn("CURRENT_account-a", bundle["data"]["current_skills"][0]["text"])
        self.assertIn("FROZEN_account-a", bundle["data"]["frozen_skills"][0]["text"])
        self.assertNotIn("CURRENT_account-a", bundle["data"]["frozen_skills"][0]["text"])
        self.assertEqual(bundle["data"]["production"]["revision_id"], self.revision_id)
        self.assertEqual(bundle["data"]["production"]["card_count"], 1)
        self.assertTrue(bundle["data"]["production"]["cards"][0]["image_prompt"])
        self.assertEqual({item["kind"] for item in bundle["data"]["image_inventory"] if item["loaded"]},
                         {"production", "skill_reference"})
        self.assertEqual(bundle["data"]["loaded_image_count"], len(bundle["images"]))
        for image in bundle["images"]:
            ref = image["attachment"]
            raw = base64.b64decode(self.chat.attachments.resolve(self.sid, ref).split(",", 1)[1])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), ref["id"])
        self.assertEqual(protected_records(self.db), before_records)
        self.assertEqual(tree_hashes(self.runs.output_root), before_files)

    def test_legacy_validation_without_image_hashes_uses_verified_fallback(self):
        with self.db.session() as session:
            session.get(ContentRevision, self.revision_id).validation_json = {}
        result = self.build()
        self.assertTrue(any(item["kind"] == "production" and item["loaded"]
                            for item in result["data"]["image_inventory"]))

    def test_reference_replaced_during_read_never_uses_bytes_under_old_skill_digest(self):
        original_read = self.catalog.read_skill_file
        reference = self.catalog.locate(self.skill_ids["account-a"]) / "assets/reference.png"
        before_rows, before_files = protected_records(self.db), tree_hashes(self.runs.output_root)
        replaced = False

        def replace_before_image_read(skill_id, path, **kwargs):
            nonlocal replaced
            if skill_id == self.skill_ids["account-a"] and path == "assets/reference.png" and not replaced:
                replaced = True
                Image.new("RGB", (24, 32), "#ee4477").save(reference)
            return original_read(skill_id, path, **kwargs)

        # Controlled interleaving, real file replacement and digest calculation.
        with patch.object(self.catalog, "read_skill_file", side_effect=replace_before_image_read):
            bundle = self.build()
        self.assertTrue(replaced)
        references = [row for row in bundle["data"]["image_inventory"] if row["kind"] == "skill_reference"]
        self.assertTrue(references)
        self.assertTrue(all(not row["loaded"] for row in references))
        self.assertTrue(any(row["kind"] == "production" and row["loaded"]
                            for row in bundle["data"]["image_inventory"]))
        self.assertTrue(any("Skill参考" in row["source"] for row in bundle["data"]["omissions"]))
        self.assertEqual(protected_records(self.db), before_rows)
        self.assertEqual(tree_hashes(self.runs.output_root), before_files)

    def select_legacy_artifact(self):
        """Real legacy hash excludes frozen Skill files; do not weaken native checks."""
        root, _, _, _ = self.app.state.queries.artifacts.locate(self.run_id, self.revision_id)
        checked = validate_artifact(root, production_protocol="legacy")
        with self.db.session() as session:
            run = session.get(ContentRun, self.run_id)
            run.input_snapshot_json = {**run.input_snapshot_json, "production_protocol": "legacy"}
            revision = session.get(ContentRevision, self.revision_id)
            revision.production_input_json = {**revision.production_input_json, "production_protocol": "legacy"}
            revision.artifact_digest = checked.artifact_digest
            revision.validation_json = checked.model_dump(mode="json")
        return root

    def test_missing_legacy_frozen_skill_is_explicit_but_verified_images_survive(self):
        root = self.select_legacy_artifact()
        (root / "skills/single/SKILL.md").unlink()
        self.assertTrue(self.app.state.queries.artifacts.projection(self.run_id, self.revision_id)["artifact_available"])
        before_rows, before_files = protected_records(self.db), tree_hashes(self.runs.output_root)
        bundle = self.build()
        self.assertEqual(bundle["data"]["frozen_skills"], [])
        self.assertIn("publish_copy", bundle["data"]["production"])
        self.assertTrue(any(row["kind"] == "production" and row["loaded"]
                            for row in bundle["data"]["image_inventory"]))
        self.assertTrue(any("frozen" in row["source"] for row in bundle["data"]["omissions"]))
        self.assertEqual(protected_records(self.db), before_rows)
        self.assertEqual(tree_hashes(self.runs.output_root), before_files)

    def test_unreadable_legacy_frozen_skill_does_not_cancel_verified_images(self):
        self.select_legacy_artifact()
        before_rows, before_files = protected_records(self.db), tree_hashes(self.runs.output_root)
        # Controlled frozen-text I/O failure; the actual legacy artifact remains valid.
        with patch("creatoros.web.chat_focus._read_managed", side_effect=FileNotFoundError("controlled disappearance")):
            bundle = self.build()
        self.assertTrue(any(row["kind"] == "production" and row["loaded"]
                            for row in bundle["data"]["image_inventory"]))
        self.assertTrue(any("frozen" in row["source"] for row in bundle["data"]["omissions"]))
        self.assertEqual(protected_records(self.db), before_rows)
        self.assertEqual(tree_hashes(self.runs.output_root), before_files)

    def test_missing_native_frozen_skill_still_fails_full_artifact_integrity(self):
        root, _, _, _ = self.app.state.queries.artifacts.locate(self.run_id, self.revision_id)
        (root / "skills/single/SKILL.md").unlink()
        bundle = self.build()
        self.assertFalse(any(row["kind"] == "production" and row["loaded"]
                             for row in bundle["data"]["image_inventory"]))
        self.assertTrue(any(row["source"] == "production" for row in bundle["data"]["omissions"]))

    def test_gif_materialization_cannot_exceed_requested_image_byte_budget(self):
        reference = self.catalog.locate(self.skill_ids["account-a"]) / "assets/00-budget.gif"
        Image.new("RGB", (1, 1), "#112233").save(reference, format="GIF", optimize=True)
        encoded = reference.read_bytes()
        with Image.open(io.BytesIO(encoded)) as image:
            converted = io.BytesIO()
            image.convert("RGBA").save(converted, format="PNG")
        self.assertGreater(len(converted.getvalue()), len(encoded), "Fixture must exercise GIF expansion")
        bundle = self.build(image_budget=len(encoded))
        self.assertLessEqual(sum(image["attachment"]["size"] for image in bundle["images"]), len(encoded))
        row = next(row for row in bundle["data"]["image_inventory"] if row.get("path") == "assets/00-budget.gif")
        self.assertFalse(row["loaded"])
        self.assertTrue(any("预算" in row["reason"] for row in bundle["data"]["omissions"]))

    def test_budget_omissions_are_explicit_and_do_not_mutate_artifacts(self):
        before = tree_hashes(self.runs.output_root)
        bundle = self.build(image_budget=0)
        self.assertEqual(bundle["images"], [])
        self.assertEqual(bundle["data"]["loaded_image_count"], 0)
        self.assertTrue(bundle["data"]["image_inventory"])
        self.assertTrue(all(not row["loaded"] for row in bundle["data"]["image_inventory"]))
        self.assertTrue(any("预算" in row["reason"] for row in bundle["data"]["omissions"]))
        with patch.object(self.builder, "MAX_TEXT_CHARS", 12):  # controlled budget fault, not model behavior
            short = self.build(image_budget=0)
        self.assertTrue(any("正文" in row["reason"] for row in short["data"]["omissions"]))
        with patch.object(self.builder, "MAX_IMAGES", 1):
            one = self.build()
        self.assertEqual(one["data"]["loaded_image_count"], 1)
        self.assertTrue(any("图片数量" in row["reason"] for row in one["data"]["omissions"]))
        self.assertEqual(tree_hashes(self.runs.output_root), before)

    def test_topic_removed_during_file_read_is_rejected_before_delivery(self):
        original_read = self.catalog.read_skill_file
        removed = False

        def remove_after_real_read(*args, **kwargs):
            nonlocal removed
            result = original_read(*args, **kwargs)
            if not removed:
                removed = True
                with self.db.session() as session:
                    session.add(TopicRemoval(topic_id="topic-a", series_id="account-a-series",
                        creator_id="account-a", status="archived", request_id=uuid4().hex))
            return result

        # Controlled concurrent-removal fault around actual catalog/SQLite reads.
        with patch.object(self.catalog, "read_skill_file", side_effect=remove_after_real_read):
            with self.assertRaises(HTTPException):
                self.build()

    def test_selected_image_file_corruption_is_reported_without_repair_or_production(self):
        root, _, _, _ = self.app.state.queries.artifacts.locate(self.run_id, self.revision_id)
        image = next((root / "images").glob("*.png"))
        image.write_bytes(b"controlled-corrupt-image")
        before_files, before_rows = tree_hashes(self.runs.output_root), protected_records(self.db)
        bundle = self.build()
        self.assertFalse(any(item["kind"] == "production" and item["loaded"]
                             for item in bundle["data"]["image_inventory"]))
        self.assertTrue(any(item["source"] == "production" for item in bundle["data"]["omissions"]))
        self.assertEqual(tree_hashes(self.runs.output_root), before_files)
        self.assertEqual(protected_records(self.db), before_rows)

    def test_safe_read_rejects_escape_and_in_root_link(self):
        outside = self.root / "outside.txt"
        outside.write_text("PRIVATE", encoding="utf-8")
        with self.assertRaises(ValueError):
            _read_managed(self.runs.output_root, outside, 100)
        link = self.runs.output_root / "linked.txt"
        try:
            link.symlink_to(outside)
        except OSError as error:
            self.skipTest(f"Host cannot create a symlink: {error}")
        with self.assertRaises(ValueError):
            _read_managed(self.runs.output_root, link, 100)

    def test_request_only_multimodal_projection_and_scoped_schemas_stay_identical(self):
        runtime = RuntimeContext(self.root, creator_id="account-a", allowed_tools=ACCOUNT_TOOLS,
                                 archive_only_reads=True)
        schemas = model_tool_schemas(tools, runtime)
        self.assertEqual(len(schemas), 34)
        self.assertTrue({"manage_skill_library", "manage_material"} <= {item["function"]["name"] for item in schemas})
        messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "查选定作品"},
                    {"role": "assistant", "tool_calls": [{"id": "read-one", "name": "get_content_run", "arguments": "{}"}]},
                    {"role": "tool", "tool_call_id": "read-one", "content": "{}"}]
        original = deepcopy(messages)
        focus = self.build()
        focused, after_schemas = build_model_context(messages, schemas, request_context=focus).to_request()
        self.assertEqual(messages, original)
        self.assertEqual(after_schemas, schemas)
        self.assertEqual(model_tool_schemas(tools, runtime), schemas)
        self.assertEqual(focused[-2]["role"], "tool")
        self.assertEqual(focused[-1]["role"], "user")
        self.assertTrue(any(block["type"] == "image_ref" for block in focused[-1]["content"]))
        provider = DeepSeekProvider(api_key="controlled-no-network-key", max_retries=0,
            image_resolver=lambda ref: self.chat.attachments.resolve(self.sid, ref))
        try:
            converted = provider._to_openai_messages(focused)
        finally:
            provider.client.close()
        self.assertTrue(any(block["type"] == "image_url" and block["image_url"]["url"].startswith("data:image/")
                            for block in converted[-1]["content"]))
        plain, plain_schemas = build_model_context(messages, schemas).to_request()
        self.assertNotIn("selected_topic_materials", json.dumps(plain))
        self.assertEqual(plain_schemas, schemas)

    @staticmethod
    def has_focus_message(messages):
        return any(block.get("type") == "text" and block.get("text", "").startswith("[本问选定作品的资料")
                   for message in messages if isinstance(message.get("content"), list)
                   for block in message["content"])

    @staticmethod
    def wait_idle(client, sid):
        deadline = monotonic() + 10
        while monotonic() < deadline:
            doc = client.get(f"/api/agent/sessions/{sid}").json()
            if doc["status"] != "running":
                return doc
            sleep(.02)
        raise AssertionError("Controlled protocol turn timed out")

    def test_real_http_replay_binds_target_and_next_query_drops_materials(self):
        before_rows, before_files = protected_records(self.db), tree_hashes(self.runs.output_root)
        with serve(self.app) as base, httpx.Client(base_url=base, trust_env=False, timeout=10) as client:
            endpoint = f"/api/agent/sessions/{self.sid}/turns"
            payload = dict(request_id=str(uuid4()), text="讨论当前选题", expected_version=0,
                           context_target=self.target)
            response = client.post(endpoint, json=payload)
            self.assertEqual(response.status_code, 202, response.text)
            first = self.wait_idle(client, self.sid)
            self.assertEqual(first["status"], "idle", first)
            replay = client.post(endpoint, json=payload)
            self.assertEqual(replay.status_code, 202, replay.text)
            other_target = dict(series_id="account-a-series", topic_id="topic-a-other")
            self.assertEqual(client.post(endpoint, json={**payload, "context_target": other_target}).status_code, 409)
            bad = client.post(endpoint, json={**payload, "request_id": str(uuid4()),
                "expected_version": first["version"], "context_target": {**self.target, "topic_id": "topic-foreign"}})
            self.assertIn(bad.status_code, (403, 404))
            self.assertEqual(len(self.captures), 1)
            second = client.post(endpoint, json=dict(request_id=str(uuid4()), text="换个普通问题",
                                                      expected_version=first["version"]))
            self.assertEqual(second.status_code, 202, second.text)
            self.assertEqual(self.wait_idle(client, self.sid)["status"], "idle")
        self.assertEqual(len(self.captures), 2)
        first_messages, first_tools = self.captures[0]
        second_messages, second_tools = self.captures[1]
        self.assertTrue(self.has_focus_message(first_messages))
        self.assertFalse(self.has_focus_message(second_messages))
        ledger = json.loads((self.root / "chats" / self.sid / "messages.json").read_text(encoding="utf-8"))
        self.assertFalse(self.has_focus_message(ledger))
        self.assertNotIn("FROZEN_account-a", json.dumps(ledger))
        self.assertFalse(any(block.get("type") == "image_ref" for message in ledger
                             if isinstance(message.get("content"), list) for block in message["content"]))
        self.assertEqual(first_tools, second_tools)
        self.assertEqual(len(first_tools), 34)
        self.assertEqual(protected_records(self.db), before_rows)
        self.assertEqual(tree_hashes(self.runs.output_root), before_files)


if __name__ == "__main__":
    unittest.main()
