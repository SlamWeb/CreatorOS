"""Explicit per-run versions over isolated SQLite/files; no paid model calls.

The real HTTP adapter test injects a producer failure after receiving its frozen
input. It checks submission/replay, not generated-image quality or a model Eval.
"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError
from sqlalchemy import func, select

from creatoros.context import RuntimeContext
from creatoros.integrations.producer_skills import ProducerSkillCatalog, _digest, skills_root_for
from creatoros.integrations.skill_pair import snapshot_pair
from creatoros.runs import ContentRunError, ContentRunService
from creatoros.runs.models import SkillOverrides
from creatoros.storage import (ContentRepository, ContentRun, ContentRunStatus, CreatorPlatform,
                              Database, Series, Topic, TopicSource, upgrade_database)
from creatoros.tools.studio import StartRunArgs, start_content_run
from creatoros.web import create_app
from creatoros.web.schemas import RunStartRequest
from tests.agent_studio_support import serve
from tests.test_production_queue import wait


class SelectedSkillRunTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.url = f"sqlite:///{(self.root / 'selection.db').as_posix()}"
        upgrade_database(self.url)
        self.db = Database(self.url)
        self.addCleanup(self.db.close)
        self.catalog = ProducerSkillCatalog(skills_root_for(self.db))
        self.base = self.skill("single", "legacy_end_to_end")
        self.mind = self.skill("mind", "mind")
        self.visual = self.skill("visual", "production")
        self.repo = ContentRepository(self.db)
        self.repo.create_creator(creator_id="creator", display_name="Local", platform=CreatorPlatform.XIAOHONGSHU)
        self.repo.create_series(series_id="single", creator_id="creator", name="Single", description="", audience="", skill_name=self.base["id"])
        with self.db.session() as session:
            session.add(Series(id="pair", creator_id="creator", name="Pair", description="", audience="",
                skill_name=None, mind_skill_id=self.mind["id"], production_skill_id=self.visual["id"]))
        for topic, series in (("topic", "single"), ("another", "single"), ("pair-topic", "pair")):
            self.repo.add_topic(topic_id=topic, series_id=series, title=topic, source=TopicSource.MANUAL)
        self.runs = ContentRunService(self.db, output_root=self.root / "outputs")

    def skill(self, name, role):
        source = self.root / name
        source.mkdir()
        (source / "assets").mkdir()
        (source / "scripts").mkdir()
        (source / "SKILL.md").write_text(
            f"---\nname: selected-{name}\ndescription: local fixture\n"
            "creatoros-output: social-content-pack.image-carousel\n---\nORIGINAL\n", encoding="utf-8")
        Image.new("RGB", (16, 16), "blue").save(source / "assets/reference.png")
        (source / "scripts/method.py").write_text("print('original')\n", encoding="utf-8")
        return self.catalog.register_local(source, role=role)

    def candidate(self, source=None):
        source = source or self.base
        current = self.catalog.read_skill_file(source["id"], "SKILL.md")
        edited = self.catalog.update_skill_file(source["id"], "SKILL.md",
            current["content"] + "\nCANDIDATE\n", expected_digest=current["digest"])
        return self.catalog.describe(edited["skill_id"])

    def selection(self, item, role="skill_id"):
        return {role: item["id"], "expected_digests": {item["id"]: item["digest"]}}

    def frozen(self, run):
        data = run.input_snapshot_json
        return self.runs.output_root / data["creator_id"] / data["series_id"] / run.id / "skill-snapshot"

    def assert_error(self, code, action):
        with self.assertRaises(ContentRunError) as raised:
            action()
        self.assertEqual(raised.exception.code, code)
        return raised.exception

    def test_candidate_body_assets_scripts_freeze_before_queue_and_ignore_later_edits(self):
        candidate = self.candidate()
        choice = self.selection(candidate)
        before_asset = (self.catalog.locate(candidate["id"]) / "assets/reference.png").read_bytes()
        run = self.runs.create("topic", skill_overrides=choice, request_id="selected-one")
        target = self.frozen(run) / "skills/single"
        self.assertEqual(_digest(target), candidate["digest"])
        self.assertEqual((target / "assets/reference.png").read_bytes(), before_asset)
        self.assertEqual((target / "scripts/method.py").read_text(), "print('original')\n")
        self.assertEqual(self.runs.repository.list_attempts(self.runs.get_active_revision(run.id).id), ())
        file = self.catalog.read_skill_file(candidate["id"], "SKILL.md")
        self.catalog.update_skill_file(candidate["id"], "SKILL.md", file["content"] + "\nLATER\n", expected_digest=file["digest"])
        Image.new("RGB", (16, 16), "red").save(self.catalog.locate(candidate["id"]) / "assets/reference.png")
        prepared = self.runs._begin_attempt(run.id, owner_id="local")
        self.assertEqual(prepared["input"].skill_digest, candidate["digest"])
        self.assertNotIn("LATER", (target / "SKILL.md").read_text(encoding="utf-8"))
        self.assertEqual((target / "assets/reference.png").read_bytes(), before_asset)
        self.assertIsNone(prepared["thread_id"])
        self.assertIsNone(prepared["previous_pages"])
        self.assertEqual(self.runs.create("topic").id, run.id)
        with self.db.session() as session:
            self.assertEqual(session.get(Series, "single").skill_name, self.base["id"])
            self.assertEqual(session.get(Series, "single").revision, 1)

    def test_partial_pair_freezes_default_companion_and_candidate_at_submission(self):
        candidate = self.candidate(self.mind)
        run = self.runs.create("pair-topic", skill_overrides=self.selection(candidate, "mind_skill_id"), request_id="partial-pair")
        data = run.input_snapshot_json
        self.assertIsNone(data["skill_name"])
        self.assertEqual(data["composition"]["mind"]["id"], candidate["id"])
        self.assertEqual(data["composition"]["production"]["id"], self.visual["id"])
        target = self.frozen(run)
        for role in ("mind", "production"):
            self.assertEqual(_digest(target / "skills" / role), data["composition"][role]["digest"])
        (self.catalog.locate(self.visual["id"]) / "assets/reference.png").write_bytes(b"later changed image")
        file = self.catalog.read_skill_file(candidate["id"], "SKILL.md")
        self.catalog.update_skill_file(candidate["id"], "SKILL.md", file["content"] + "\nLATER\n", expected_digest=file["digest"])
        prepared = self.runs._begin_attempt(run.id, owner_id="local")
        self.assertEqual(prepared["input"].composition.model_dump(mode="json"), data["composition"])
        self.assertIsNone(prepared["thread_id"])
        with self.db.session() as session:
            series = session.get(Series, "pair")
            self.assertEqual((series.mind_skill_id, series.production_skill_id, series.revision),
                             (self.mind["id"], self.visual["id"], 1))

    def test_selection_replay_is_atomic_and_conflicting_ids_or_digests_rejected(self):
        candidate = self.candidate()
        choice = self.selection(candidate)
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(lambda _: self.runs.create("topic", skill_overrides=choice, request_id="same-key").id, range(8)))
        self.assertEqual(len(set(ids)), 1)
        file = self.catalog.read_skill_file(candidate["id"], "SKILL.md")
        changed = self.catalog.update_skill_file(candidate["id"], "SKILL.md", file["content"] + "changed", expected_digest=file["digest"])
        # Response-loss replay denotes original bytes even after the source changed.
        self.assertEqual(self.runs.create("topic", skill_overrides=choice, request_id="same-key").id, ids[0])
        new_choice = {**choice, "expected_digests": {candidate["id"]: changed["digest"]}}
        for topic, selection in (("topic", new_choice), ("topic", self.selection(self.base)), ("another", choice)):
            self.assert_error("idempotency_conflict", lambda: self.runs.create(topic, skill_overrides=selection, request_id="same-key"))
        with self.db.session() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(ContentRun)), 1)

    def test_explicit_single_or_complete_pair_can_replace_this_runs_binding_shape(self):
        single = self.candidate()
        run = self.runs.create("pair-topic", skill_overrides=self.selection(single), request_id="pair-as-single")
        self.assertEqual(run.input_snapshot_json["skill_name"], single["id"])
        self.assertIsNone(run.input_snapshot_json.get("composition"))
        self.runs._begin_attempt(run.id, owner_id="single-owner")
        candidate = self.candidate(self.visual)
        pair = {"mind_skill_id": self.mind["id"], "production_skill_id": candidate["id"],
                "expected_digests": {self.mind["id"]: self.mind["digest"], candidate["id"]: candidate["digest"]}}
        paired = self.runs.create("topic", skill_overrides=pair, request_id="single-as-pair")
        self.assertIsNone(paired.input_snapshot_json["skill_name"])
        self.assertEqual(paired.input_snapshot_json["composition"]["production"]["id"], candidate["id"])
        self.runs._begin_attempt(paired.id, owner_id="pair-owner")
        with self.db.session() as session:
            self.assertEqual(session.get(Series, "single").skill_name, self.base["id"])
            self.assertEqual(session.get(Series, "pair").production_skill_id, self.visual["id"])

    def test_process_restart_uses_persisted_snapshot_not_new_candidate_body(self):
        candidate = self.candidate()
        choice = self.selection(candidate)
        run = self.runs.create("topic", skill_overrides=choice, request_id="restart-selection")
        file = self.catalog.read_skill_file(candidate["id"], "SKILL.md")
        self.catalog.update_skill_file(candidate["id"], "SKILL.md", file["content"] + "\nAFTER_RESTART\n", expected_digest=file["digest"])
        restarted_db = Database(self.url)
        self.addCleanup(restarted_db.close)
        restarted = ContentRunService(restarted_db, output_root=self.runs.output_root)
        self.assertEqual(restarted.create("topic", skill_overrides=choice, request_id="restart-selection").id, run.id)
        prepared = restarted._begin_attempt(run.id, owner_id="new-process")
        self.assertEqual(prepared["input"].skill_digest, candidate["digest"])
        frozen = self.frozen(run) / "skills/single"
        self.assertNotIn("AFTER_RESTART", (frozen / "SKILL.md").read_text(encoding="utf-8"))

    def test_stale_digest_and_missing_role_do_not_create_run(self):
        candidate = self.candidate()
        file = self.catalog.read_skill_file(candidate["id"], "SKILL.md")
        self.catalog.update_skill_file(candidate["id"], "SKILL.md", file["content"] + "changed", expected_digest=file["digest"])
        self.assert_error("invalid_skill", lambda: self.runs.create("topic", skill_overrides=self.selection(candidate), request_id="stale"))
        self.assert_error("invalid_skill_selection", lambda: self.runs.create("topic", skill_overrides=self.selection(self.mind, "mind_skill_id"), request_id="incomplete"))
        self.assert_error("invalid_skill", lambda: self.runs.create("topic", skill_overrides=self.selection(self.mind), request_id="wrong-role"))
        with self.db.session() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(ContentRun)), 0)

    def test_frozen_tampering_or_loss_fails_closed_without_rereading_candidate(self):
        candidate = self.candidate()
        run = self.runs.create("topic", skill_overrides=self.selection(candidate), request_id="tamper")
        (self.frozen(run) / "skills/single/assets/reference.png").write_bytes(b"tamper")
        self.assert_error("invalid_skill_snapshot", lambda: self.runs._begin_attempt(run.id, owner_id="local"))
        other = self.runs.create("another", skill_overrides=self.selection(candidate), request_id="missing")
        # Simulate a lost directory without deleting any formal files.
        (self.frozen(other) / "skills/single").rename(self.frozen(other) / "skills/moved")
        self.assert_error("invalid_skill_snapshot", lambda: self.runs._begin_attempt(other.id, owner_id="local"))
        self.assertEqual(self.runs.repository.list_attempts(self.runs.get_active_revision(run.id).id), ())
        self.assertEqual(self.runs.repository.list_attempts(self.runs.get_active_revision(other.id).id), ())

    def test_edit_between_resolution_and_freezing_cannot_substitute_new_bytes(self):
        candidate = self.candidate()
        resolve = ProducerSkillCatalog.resolve_selected_version

        def raced(catalog, skill_id, *, expected_digest):
            directory = resolve(catalog, skill_id, expected_digest=expected_digest)
            (directory / "assets/reference.png").write_bytes(b"concurrent edit after digest check")
            return directory

        with patch.object(ProducerSkillCatalog, "resolve_selected_version", raced):
            self.assert_error("invalid_skill_snapshot", lambda: self.runs.create("topic",
                skill_overrides=self.selection(candidate), request_id="raced-copy"))
        with self.db.session() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(ContentRun)), 0)

    def test_candidate_permission_is_only_explicit_and_never_changes_bindings(self):
        candidate = self.candidate()
        with self.assertRaises(ValueError):
            self.catalog.resolve(candidate["id"])
        mind_candidate = self.candidate(self.mind)
        with self.assertRaises(ValueError):
            snapshot_pair(self.catalog, mind_candidate["id"], self.visual["id"], native=True)
        self.assert_error("invalid_request", lambda: self.runs.create("topic", skill_overrides=self.selection(candidate)))
        initial = self.runs.create("topic")
        self.assert_error("run_exists", lambda: self.runs.create("topic", skill_overrides=self.selection(candidate), request_id="new-key"))
        self.assert_error("run_active", lambda: self.runs.restart_fresh(initial.id, expected_version=initial.version,
            request_id="fresh-active", skill_overrides=self.selection(candidate)))
        with self.db.session() as session:
            self.assertEqual(session.get(Series, "single").skill_name, self.base["id"])

    def test_fresh_selected_candidate_has_clean_thread_and_preserves_old_run_files(self):
        old = self.runs.create("topic")
        previous = self.runs._begin_attempt(old.id, owner_id="old")
        previous["directory"].mkdir(parents=True)
        old_file = previous["directory"] / "already-generated.png"
        Image.new("RGB", (16, 16), "green").save(old_file)
        old_bytes = old_file.read_bytes()
        with self.db.session() as session:
            record = session.get(ContentRun, old.id)
            record.status = ContentRunStatus.FAILED
            record.producer_thread_id = "old-thread"
        old = self.runs.get(old.id)
        before_events = [event.id for event in self.runs.repository.list_events(old.id)]
        candidate = self.candidate()
        selected = self.runs.restart_fresh(old.id, expected_version=old.version, request_id="fresh-selection",
            skill_overrides=self.selection(candidate))
        prepared = self.runs._begin_attempt(selected.id, owner_id="new")
        self.assertIsNone(prepared["thread_id"])
        self.assertIsNone(prepared["previous_pages"])
        self.assertIsNone(prepared["instruction"])
        self.assertEqual(prepared["input"].skill_name, candidate["id"])
        self.assertEqual(old_file.read_bytes(), old_bytes)
        after = self.runs.get(old.id)
        self.assertEqual((after.version, after.status, after.producer_thread_id, after.input_snapshot_json),
            (old.version, old.status, old.producer_thread_id, old.input_snapshot_json))
        self.assertEqual([event.id for event in self.runs.repository.list_events(old.id)], before_events)
        self.assertEqual(self.runs.restart_fresh(old.id, expected_version=old.version, request_id="fresh-selection",
            skill_overrides=self.selection(candidate)).id, selected.id)
        self.assert_error("idempotency_conflict", lambda: self.runs.restart_fresh(old.id,
            expected_version=old.version, request_id="fresh-selection", skill_overrides=self.selection(self.base)))

    def test_strict_shapes_reject_ambiguous_selection_and_paths(self):
        digest = self.base["digest"]
        for data in ({}, {"skill_id": self.base["id"], "expected_digests": {}},
                     {"skill_id": self.base["id"], "mind_skill_id": self.mind["id"], "expected_digests": {}},
                     {"skill_id": "D:/private/skill", "expected_digests": {"D:/private/skill": digest}},
                     {"skill_id": self.base["id"], "expected_digests": {self.base["id"]: "bad"}}):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                SkillOverrides.model_validate(data)
        choice = self.selection(self.base)
        with self.assertRaises(ValidationError):
            RunStartRequest(topic_id="topic", skill_overrides=choice)
        with self.assertRaises(ValidationError):
            StartRunArgs(topic_ids=["topic"], skill_overrides=choice)
        with self.assertRaises(ValidationError):
            StartRunArgs(series_id="single", title="direct", attachment_ids=["image"], skill_overrides=choice)
        self.assertIsNotNone(RunStartRequest(topic_id="topic", request_id="stable", skill_overrides=choice).skill_overrides)

    def test_http_create_selected_version_replays_and_reports_conflict(self):
        candidate = self.candidate()
        app = create_app(database=self.db, run_service=self.runs)
        with TestClient(app) as client:
            session = client.post("/api/agent/sessions", json={"creator_id": "creator"}).json()["id"]
            headers = {"x-creatoros-agent-session": session}
            payload = {"topic_id": "topic", "request_id": "http-selection", "skill_overrides": self.selection(candidate)}
            first = client.post("/api/runs", json=payload, headers=headers)
            self.assertEqual(first.status_code, 201, first.text)
            replay = client.post("/api/runs", json=payload, headers=headers)
            self.assertEqual(replay.status_code, 200, replay.text)
            self.assertEqual(first.json()["id"], replay.json()["id"])
            conflict = client.post("/api/runs", json={**payload, "skill_overrides": self.selection(self.base)}, headers=headers)
            self.assertEqual(conflict.status_code, 409, conflict.text)
            self.assertEqual(conflict.json()["error"]["code"], "idempotency_conflict")

    def test_complete_override_does_not_require_unselected_default_files(self):
        candidate = self.candidate()
        app = create_app(database=self.db, run_service=self.runs)
        with TestClient(app) as client:
            session = client.post("/api/agent/sessions", json={"creator_id": "creator"}).json()["id"]
            headers = {"x-creatoros-agent-session": session}
            # The original family registry is valid, but its default body is
            # unavailable. This task selected a separate complete candidate.
            directory = self.catalog.locate(self.base["id"])
            (directory / "SKILL.md").rename(directory / "temporarily-missing.md")
            result = client.post("/api/runs", headers=headers, json={"topic_id": "topic",
                "request_id": "candidate-independent", "skill_overrides": self.selection(candidate)})
            self.assertEqual(result.status_code, 201, result.text)
            self.assertEqual(result.json()["input_snapshot"]["skill_name"], candidate["id"])
            with self.db.session() as db:
                self.assertEqual(db.get(Series, "single").skill_name, self.base["id"])

    def test_agent_http_adapter_uses_stable_identity_and_exact_frozen_candidate(self):
        class ReceiveThenFail:
            def __init__(self):
                self.calls = []

            def produce_to(self, **request):
                self.calls.append(request)
                raise RuntimeError("isolated failure after receiving the selected input")

        producer = ReceiveThenFail()
        self.runs.producer_factory = lambda: producer
        candidate = self.candidate()
        app = create_app(database=self.db, run_service=self.runs)
        with serve(app) as base:
            context = RuntimeContext(self.root, studio_url=base, user_request_id="same-user-turn",
                                     session_file=self.root / "session.json")
            result = start_content_run(topic_id="topic", skill_overrides=self.selection(candidate), context=context)
            self.assertFalse(result.is_error, result.content)
            identifier = json.loads(result.content)["run_id"]
            wait(lambda: self.runs.get(identifier).status is ContentRunStatus.FAILED)
            self.assertEqual(len(producer.calls), 1)
            request = producer.calls[0]
            self.assertEqual(request["skill_name"], candidate["id"])
            self.assertEqual(_digest(request["skill_directory"]), candidate["digest"])
            replay = start_content_run(topic_id="topic", skill_overrides=self.selection(candidate), context=context)
            self.assertFalse(replay.is_error, replay.content)
            self.assertEqual(json.loads(replay.content)["run_id"], identifier)
            conflict = start_content_run(topic_id="topic", skill_overrides=self.selection(self.base), context=context)
            self.assertTrue(conflict.is_error)
            self.assertEqual(len(producer.calls), 1)


if __name__ == "__main__":
    unittest.main()
