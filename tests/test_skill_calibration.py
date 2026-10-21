"""Real isolated SQLite/HTTP/files; controlled models only test contracts/faults."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from creatoros.integrations.producer_skills import ProducerSkillCatalog, SkillInstallService, _digest, freeze_skill
from creatoros.integrations.skill_calibration import signature
from creatoros.integrations.skill_extraction import ExtractionError, SkillExtractionService
from creatoros.runs import ContentRunService
from creatoros.storage import Base, ContentRun, Database, Series, SkillCalibration, Topic, upgrade_database
from creatoros.web import create_app
from tests.skill_calibration_fixture import (ControlledCalibrationProducer, calibration_evidence,
    controlled_calibration_revise, seed_calibration, tree_hashes)


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.url = f"sqlite:///{(self.root / 'test.db').as_posix()}"
        upgrade_database(self.url)
        self.db = Database(self.url)
        self.catalog = ProducerSkillCatalog(self.root / "skills")
        self.extractions = SkillExtractionService(self.catalog)
        self.runs = ContentRunService(self.db, output_root=self.root / "outputs")
        self.app = create_app(database=self.db, run_service=self.runs,
            skill_install_service=SkillInstallService(self.catalog), skill_extraction_service=self.extractions)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.fixture = seed_calibration(self.app, self.root)
        self.service = self.app.state.skill_calibrations
        self.baseline = calibration_evidence(self.app)

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.db.close()
        self.temp.cleanup()

    def payload(self, **changes):
        f = self.fixture
        result = dict(request_id=uuid4().hex, series_id=f["series_id"], skill_id=f["skill_id"],
            expected_skill_digest=f["skill_digest"], artifacts=[dict(run_id=f["run_id"],
                revision_id=f["revision_id"], artifact_digest=f["artifact_digest"], order=1,
                rating="bad", comment="结尾只是说教，没有具体行动。")],
            references=[dict(upload_id=f["reference_upload"]["id"], rating="good",
                             comment="用开头道具完成行动，有呼应。")])
        result.update(changes)
        return result

    def create(self, payload=None):
        response = self.client.post("/api/skill-calibrations", json=payload or self.payload())
        self.assertEqual(response.status_code, 202, response.text)
        identifier = response.json()["id"]
        self.extractions.worker.join(10)
        result = self.client.get("/api/skill-calibrations/" + identifier)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["status"], "ready", result.text)
        return result.json()

    def trial(self, calibration):
        job = calibration["job"]
        response = self.client.post(f"/api/skill-extractions/{job['id']}/trials", json={
            "request_id": uuid4().hex, "expected_digest": job["digest"], "topic": "开场的小蛋糕与结尾的分享"})
        self.assertEqual(response.status_code, 202, response.text)
        self.extractions.worker.join(10)
        current = self.client.get("/api/skill-calibrations/" + calibration["id"]).json()
        self.assertEqual(current["job"]["trials"][-1]["status"], "completed", current)
        return current

    def apply_payload(self, calibration):
        return dict(request_id=uuid4().hex, expected_digest=calibration["job"]["digest"],
            expected_skill_digest=calibration["base_skill_digest"], trial_id=calibration["job"]["trials"][-1]["id"])

    def assert_formal_unchanged(self, target=True, switched=False):
        actual = calibration_evidence(self.app)
        if switched:
            # A confirmed version changes exactly this Series' version binding,
            # CAS revision and update timestamp. Every other original column,
            # account, topic, Run, Revision and Attempt remains byte-for-byte data.
            series_id = self.fixture["series_id"]
            before = self.baseline["protected_records"]["series"][series_id]
            after = actual["protected_records"]["series"][series_id]
            self.assertNotEqual(after["mind_skill_id"], before["mind_skill_id"])
            self.assertEqual(after["revision"], before["revision"] + 1)
            self.assertTrue(self.catalog.same_skill_family(after["mind_skill_id"], before["mind_skill_id"]))
            self.assertEqual(self.catalog.describe(after["mind_skill_id"])["version_status"], "released")
            allowed = {"mind_skill_id", "revision", "updated_at"}
            self.assertEqual({key: value for key, value in after.items() if key not in allowed},
                             {key: value for key, value in before.items() if key not in allowed})
            actual["protected_records"]["series"][series_id] = before
        for key in ("counts", "protected_records", "companion_files", "artifact_files"):
            self.assertEqual(actual[key], self.baseline[key], key)
        if target:
            self.assertEqual(actual["target_files"], self.baseline["target_files"])

    def test_migration_zero_metadata_drift_and_empty(self):
        with self.db.engine.connect() as connection:
            self.assertEqual(compare_metadata(MigrationContext.configure(connection), Base.metadata), [])
        with self.db.session() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(SkillCalibration)), 0)

    def test_create_receives_actual_images_context_and_frozen_base(self):
        calls = []
        async def capture(directory, images, mode, instruction, cancel, on_thread, skills):
            calls.append((directory, list(images), mode, skills))
            context = json.loads((directory / "calibration_context.json").read_text(encoding="utf-8"))
            self.assertEqual(len(context["works"][0]["pages"]), 1)
            self.assertEqual(context["labels"][0]["rating"], "bad")
            self.assertEqual(context["references"][0]["rating"], "good")
            self.assertTrue(all(path.is_file() and path.read_bytes() for path in images))
            return await controlled_calibration_revise(directory, images, mode, instruction, cancel, on_thread, skills)
        self.extractions.reviser = capture
        calibration = self.create()
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0][1]), 2)
        self.assertIn("用具体事件讲故事", calibration["base_skill_text"])
        self.assertIn("受控校准改稿", calibration["diff"])
        self.assertEqual(calibration["job"]["task_kind"], "calibrate")
        self.assert_formal_unchanged()

    def test_old_revision_single_page_label_keeps_complete_distinct_multi_page_work(self):
        import hashlib
        self.fixture = seed_calibration(self.app, self.root, multi_revision=True)
        self.baseline = calibration_evidence(self.app)
        f = self.fixture
        with self.db.session() as session:
            self.assertEqual(session.get(ContentRun, f["run_id"]).active_revision_number, 2)
        old_root, _, old_data, _ = self.service.artifacts.locate(f["run_id"], f["revision_id"])
        new_root, _, new_data, _ = self.service.artifacts.locate(f["run_id"], f["newer_revision_id"])
        old_pack = self.service.artifacts.pack(old_root, old_data)
        new_pack = self.service.artifacts.pack(new_root, new_data)
        old_hashes = [hashlib.sha256((old_root / card.image_path).read_bytes()).hexdigest()
                      for card in old_pack.cards]
        new_hashes = [hashlib.sha256((new_root / card.image_path).read_bytes()).hexdigest()
                      for card in new_pack.cards]
        self.assertEqual(len(set(old_hashes)), 3)
        self.assertEqual(len(set(new_hashes)), 2)
        self.assertTrue(set(old_hashes).isdisjoint(new_hashes))
        captured = []
        async def capture(directory, images, mode, instruction, cancel, on_thread, skills):
            context = json.loads((directory / "calibration_context.json").read_text(encoding="utf-8"))
            captured.append(context)
            self.assertEqual(len(context["works"]), 1)
            work = context["works"][0]
            self.assertEqual(work["revision_id"], f["revision_id"])
            self.assertEqual([page["order"] for page in work["pages"]], [1, 2, 3])
            self.assertEqual([page["sha256"] for page in work["pages"]], old_hashes)
            self.assertEqual(work["publish_copy"], old_pack.publish_copy.model_dump())
            self.assertEqual([page["body"] for page in work["pages"]], [card.body for card in old_pack.cards])
            self.assertEqual(context["labels"][0]["order"], 2)
            self.assertEqual([hashlib.sha256(path.read_bytes()).hexdigest() for path in images], old_hashes)
            return await controlled_calibration_revise(directory, images, mode, instruction, cancel, on_thread, skills)
        self.extractions.reviser = capture
        payload = self.payload(references=[])
        payload["artifacts"][0]["order"] = 2
        calibration = self.create(payload)
        self.assertEqual(len(captured), 1)
        self.assertEqual([card["order"] for card in calibration["artifacts"][0]["cards"]], [1, 2, 3])
        calibration = self.trial(calibration)
        response = self.client.post(f"/api/skill-calibrations/{calibration['id']}/apply",
                                    json=self.apply_payload(calibration))
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_formal_unchanged(switched=True)

    def test_database_protection_detects_changed_original_row_even_when_counts_match(self):
        with self.db.session() as session:
            run = session.get(ContentRun, self.fixture["run_id"])
            topic = session.get(Topic, run.topic_id)
            topic.brief = "受控损坏：行数没变，但原文被覆盖。"
        actual = calibration_evidence(self.app)
        self.assertEqual(actual["counts"], self.baseline["counts"])
        with self.assertRaisesRegex(AssertionError, "protected_records"):
            self.assert_formal_unchanged()

    def test_idempotent_create_conflict_and_save_bypass(self):
        payload = self.payload()
        calibration = self.create(payload)
        repeat = self.client.post("/api/skill-calibrations", json=payload)
        self.assertEqual(repeat.status_code, 202, repeat.text)
        self.assertEqual(repeat.json()["id"], calibration["id"])
        altered = {**payload, "instruction": "不同要求"}
        self.assertEqual(self.client.post("/api/skill-calibrations", json=altered).status_code, 409)
        denied = self.client.post(f"/api/skill-extractions/{calibration['job_id']}/save",
                                 json={"expected_digest": calibration["job"]["digest"]})
        self.assertEqual(denied.status_code, 409, denied.text)
        self.assert_formal_unchanged()

    def test_apply_requires_successful_current_trial(self):
        calibration = self.create()
        response = self.client.post(f"/api/skill-calibrations/{calibration['id']}/apply", json={
            "request_id": uuid4().hex, "expected_digest": calibration["job"]["digest"],
            "expected_skill_digest": calibration["base_skill_digest"], "trial_id": "a" * 64})
        self.assertEqual(response.status_code, 409, response.text)
        self.assert_formal_unchanged()

    def test_mind_trial_frozen_visual_clean_thread_and_confirmed_apply(self):
        # Another column deliberately shares v1; confirming this calibration
        # must not silently upgrade that second column.
        other_id = "other-column-sharing-v1"
        with self.db.session() as session:
            current = session.get(Series, self.fixture["series_id"])
            session.add(Series(id=other_id, creator_id=current.creator_id, name="另一个共享栏目",
                description="保留定位", audience="保留受众", skill_name=None,
                mind_skill_id=current.mind_skill_id, production_skill_id=current.production_skill_id))
        with self.db.session() as session:
            other = session.get(Series, other_id)
            other_before = {column.name: getattr(other, column.name) for column in Series.__table__.columns}
        calibration = self.create()
        producer = ControlledCalibrationProducer()
        self.extractions.producer_factory = lambda: producer
        calibration = self.trial(calibration)
        call = producer.calls[0]
        self.assertEqual(call["composition"].adapter, "native-carousel-v1")
        self.assertNotIn("thread_id", call)
        self.assertNotIn("previous_directory", call)
        self.assertEqual(call["composition"].production.id, self.fixture["companion_id"])
        request = self.apply_payload(calibration)
        endpoint = f"/api/skill-calibrations/{calibration['id']}/apply"
        response = self.client.post(endpoint, json=request)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["skill_id"], self.fixture["skill_id"])
        receipt = self.service._row(calibration["id"]).apply_json
        version_id = receipt["version_skill_id"]
        self.assertNotEqual(version_id, self.fixture["skill_id"])
        self.assertEqual(self.catalog.describe(version_id)["digest"], result["applied_digest"])
        self.assertEqual(self.catalog.describe(self.fixture["skill_id"])["digest"], calibration["base_skill_digest"])
        self.assertEqual(tree_hashes(self.catalog.original(self.fixture["skill_id"], calibration["base_skill_digest"])), self.fixture["baseline_files"])
        self.assertEqual(self.client.post(endpoint, json=request).json()["applied_digest"], result["applied_digest"])
        self.assertEqual(self.client.post(endpoint, json={**request, "trial_id": "b" * 64}).status_code, 409)
        self.assert_formal_unchanged(switched=True)
        with self.db.session() as session:
            other = session.get(Series, other_id)
            self.assertEqual({column.name: getattr(other, column.name) for column in Series.__table__.columns}, other_before)
        self.assertEqual(self.client.post(f"/api/skill-extractions/{calibration['job_id']}/draft", json={
            "expected_digest": calibration["job"]["digest"], "skills": calibration["job"]["skills"]}).status_code, 409)

    def test_wrong_series_digest_and_invalid_page_rejected_before_worker(self):
        base = self.payload()
        with self.db.session() as session:
            original = session.get(Series, self.fixture["series_id"])
            session.add(Series(id="other-series", creator_id=original.creator_id, name="Other", description="", audience="",
                skill_name=None, mind_skill_id=original.mind_skill_id, production_skill_id=original.production_skill_id))
        wrong_series = {**base, "series_id": "other-series"}
        self.assertEqual(self.client.post("/api/skill-calibrations", json=wrong_series).status_code, 403)
        bad_digest = {**base, "artifacts": [{**base["artifacts"][0], "artifact_digest": "a" * 64}]}
        self.assertEqual(self.client.post("/api/skill-calibrations", json=bad_digest).status_code, 409)
        bad_page = {**base, "artifacts": [{**base["artifacts"][0], "order": 99}]}
        self.assertEqual(self.client.post("/api/skill-calibrations", json=bad_page).status_code, 422)
        self.assertIsNone(self.extractions.worker)
        self.assert_formal_unchanged()

    def test_changed_draft_invalidates_prior_trial(self):
        calibration = self.trial(self.create())
        request = self.apply_payload(calibration)
        job = calibration["job"]
        skills = [{**job["skills"][0], "skill_md": job["skills"][0]["skill_md"] + "\n新的要求\n"}]
        edited = self.client.post(f"/api/skill-extractions/{job['id']}/draft", json={
            "expected_digest": job["digest"], "skills": skills})
        self.assertEqual(edited.status_code, 200, edited.text)
        request["expected_digest"] = edited.json()["digest"]
        response = self.client.post(f"/api/skill-calibrations/{calibration['id']}/apply", json=request)
        self.assertEqual(response.status_code, 409, response.text)
        self.assert_formal_unchanged()

    def test_changed_shared_skill_cas_preserves_other_edit(self):
        calibration = self.trial(self.create())
        path = Path(self.fixture["target_path"]) / "SKILL.md"
        path.write_text(path.read_text(encoding="utf-8") + "\n另一次人工编辑\n", encoding="utf-8")
        changed = tree_hashes(path.parent)
        response = self.client.post(f"/api/skill-calibrations/{calibration['id']}/apply", json=self.apply_payload(calibration))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(tree_hashes(path.parent), changed)
        self.assert_formal_unchanged(target=False)

    def test_trial_tampering_and_name_change_rejected(self):
        calibration = self.trial(self.create())
        job = calibration["job"]
        renamed = [{**job["skills"][0], "skill_md": job["skills"][0]["skill_md"].replace(
            "name: " + job["skills"][0]["name"], "name: altered-name")}]
        edited = self.client.post(f"/api/skill-extractions/{job['id']}/draft", json={
            "expected_digest": job["digest"], "skills": renamed})
        self.assertEqual(edited.status_code, 422, edited.text)
        self.assertEqual(self.extractions.get(job["id"])["digest"], job["digest"])
        from creatoros.integrations.skill_workbench import trial_directory
        root = trial_directory(self.extractions, job["id"], job["trials"][-1]["id"])
        (root / "images/01-cover.png").write_bytes(b"tampered")
        response = self.client.post(f"/api/skill-calibrations/{calibration['id']}/apply", json=self.apply_payload(calibration))
        self.assertEqual(response.status_code, 409, response.text)
        self.assert_formal_unchanged()

    def test_agent_cannot_create_or_apply(self):
        response = self.client.post("/api/skill-calibrations", json=self.payload(),
                                    headers={"x-creatoros-agent-session": "overview"})
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIsNone(self.extractions.worker)

    def test_real_loopback_http_closed_loop_and_frozen_evidence_preview(self):
        import httpx
        from fastapi import FastAPI
        from creatoros.web.calibration_routes import calibration_routes
        from creatoros.web.extraction_routes import extraction_routes
        from tests.agent_studio_support import serve
        app = FastAPI()
        app.include_router(calibration_routes(self.service))
        app.include_router(extraction_routes(self.extractions))
        with serve(app) as base_url, httpx.Client(base_url=base_url) as client:
            response = client.post("/api/skill-calibrations", json=self.payload())
            self.assertEqual(response.status_code, 202, response.text)
            identifier = response.json()["id"]
            self.extractions.worker.join(10)
            calibration = client.get("/api/skill-calibrations/" + identifier).json()
            self.assertEqual(calibration["status"], "ready", calibration)
            for url in [calibration["artifacts"][0]["cards"][0]["url"], calibration["references"][0]["url"]]:
                result = client.get(url)
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(result.headers["content-type"], "image/png")
            job = calibration["job"]
            trial = client.post(f"/api/skill-extractions/{job['id']}/trials", json={
                "request_id": uuid4().hex, "expected_digest": job["digest"], "topic": "HTTP接线"})
            self.assertEqual(trial.status_code, 202, trial.text)
            self.extractions.worker.join(10)
            calibration = client.get("/api/skill-calibrations/" + identifier).json()
            self.assertEqual(calibration["job"]["trials"][-1]["status"], "completed", calibration)
            applied = client.post(f"/api/skill-calibrations/{identifier}/apply", json=self.apply_payload(calibration))
            self.assertEqual(applied.status_code, 200, applied.text)
            self.assertEqual(applied.json()["status"], "applied")
        self.assert_formal_unchanged(switched=True)

    def test_failed_revise_retains_original_draft_and_failure_evidence(self):
        async def fail(*args, **kwargs):
            raise RuntimeError("injected controlled model failure")
        self.extractions.reviser = fail
        response = self.client.post("/api/skill-calibrations", json=self.payload())
        self.assertEqual(response.status_code, 202, response.text)
        self.extractions.worker.join(10)
        calibration = self.service.get(response.json()["id"])
        self.assertEqual(calibration["status"], "ready")
        self.assertIn("原草稿保留", calibration["error"])
        self.assertEqual(calibration["base_skill_text"], calibration["job"]["skills"][0]["skill_md"])
        errors = list(self.extractions._path("jobs", calibration["job_id"]).glob("revisions/*/error.txt"))
        self.assertEqual(len(errors), 1)
        self.assert_formal_unchanged()

    def test_busy_worker_queues_second_calibration_without_interrupting_first(self):
        release = threading.Event()
        async def waiting(*args):
            while not release.is_set():
                await asyncio.sleep(.01)
            return await controlled_calibration_revise(*args)
        self.extractions.reviser = waiting
        try:
            first = self.client.post("/api/skill-calibrations", json=self.payload())
            self.assertEqual(first.status_code, 202, first.text)
            second = self.client.post("/api/skill-calibrations", json=self.payload())
            self.assertEqual(second.status_code, 202, second.text)
            self.assertEqual(second.json()["job"]["queue"]["state"], "queued")
            self.assertFalse(self.extractions.cancel_event.is_set())
            with self.db.session() as session:
                self.assertEqual(session.scalar(select(func.count()).select_from(SkillCalibration)), 2)
        finally:
            release.set()
            self.extractions.worker.join(10)
        for response in (first, second):
            result = self.client.get("/api/skill-calibrations/" + response.json()["id"])
            self.assertEqual(result.json()["status"], "ready", result.text)
            self.assertEqual(result.json()["job"]["queue"]["state"], "completed")
        self.assert_formal_unchanged()

    def test_visual_candidate_uses_frozen_mind_without_fusion(self):
        f = self.fixture
        payload = self.payload()
        payload["skill_id"] = f["companion_id"]
        payload["expected_skill_digest"] = self.catalog.describe(f["companion_id"])["digest"]
        calibration = self.create(payload)
        self.assertEqual(calibration["job"]["skills"][0]["role"], "production")
        producer = ControlledCalibrationProducer()
        self.extractions.producer_factory = lambda: producer
        calibration = self.trial(calibration)
        self.assertEqual(producer.calls[0]["composition"].mind.id, f["skill_id"])
        self.assertEqual(producer.calls[0]["composition"].production.name, calibration["job"]["skills"][0]["name"])
        self.assert_formal_unchanged()

    def test_companion_later_edit_does_not_change_frozen_trial_inputs(self):
        calibration = self.create()
        companion = self.catalog.locate(self.fixture["companion_id"])
        old_digest = _digest(companion)
        path = companion / "SKILL.md"
        path.write_text(path.read_text(encoding="utf-8") + "\n后来改过的搭档\n", encoding="utf-8")
        producer = ControlledCalibrationProducer()
        self.extractions.producer_factory = lambda: producer
        self.trial(calibration)
        self.assertEqual(producer.calls[0]["composition"].production.digest, old_digest)
        self.assertNotEqual(_digest(companion), old_digest)
        self.assertEqual(calibration_evidence(self.app)["artifact_files"], self.baseline["artifact_files"])

    def test_version_switch_failure_keeps_old_binding_and_restart_replays_intent(self):
        from creatoros.integrations.skill_version_service import SkillVersionService
        calibration = self.trial(self.create())
        payload = self.apply_payload(calibration)
        endpoint = f"/api/skill-calibrations/{calibration['id']}/apply"
        with patch.object(SkillVersionService, "switch", side_effect=OSError("injected switch DB unavailable")):
            response = self.client.post(endpoint, json=payload)
        self.assertEqual(response.status_code, 409, response.text)
        row = self.service._row(calibration["id"])
        self.assertEqual(row.status, "applying")
        self.assertEqual(row.apply_json["kind"], "skill_version")
        version_id = row.apply_json["version_skill_id"]
        self.assertEqual(self.catalog.describe(version_id)["version_status"], "released")
        self.assert_formal_unchanged()
        # The user already authorized this exact switch. Restart only completes
        # its persisted intent, without repeating a model/trial or editing old files.
        with patch.object(self.extractions, "reviser", side_effect=AssertionError("must not call SDK")), \
                patch.object(self.extractions, "producer_factory", side_effect=AssertionError("must not regenerate")):
            self.service.start()
        self.assertEqual(self.service._row(calibration["id"]).status, "applied")
        retry = self.client.post(endpoint, json=payload)
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.json()["apply_phase"], "applied")
        self.assertEqual(len(retry.json()["job"]["trials"]), 1)
        self.assertEqual(len(self.catalog.list_skill_versions(version_id)["items"]), 2)
        self.assert_formal_unchanged(switched=True)

    def test_finalize_failure_preserves_committed_switch_and_postcommit_read_does_not_revert_it(self):
        calibration = self.trial(self.create())
        payload = self.apply_payload(calibration)
        endpoint = f"/api/skill-calibrations/{calibration['id']}/apply"
        original = self.service._update
        def fail_commit(identifier, **changes):
            if changes.get("status") == "applied":
                raise OSError("injected DB commit failure")
            return original(identifier, **changes)
        with patch.object(self.service, "_update", fail_commit):
            response = self.client.post(endpoint, json=payload)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.service._row(calibration["id"]).status, "applying")
        self.assert_formal_unchanged(switched=True)
        # The switch receipt committed even though the calibration projection
        # did not. Idempotent recovery must not increment Series.revision twice.
        self.service.start()
        self.assertEqual(self.service._row(calibration["id"]).status, "applied")
        with patch.object(self.service, "get", side_effect=OSError("injected response read failure")):
            response = self.client.post(endpoint, json=payload)
        self.assertEqual(response.status_code, 409, response.text)
        row = self.service._row(calibration["id"])
        self.assertEqual(row.status, "applied")
        self.assertEqual(self.catalog.describe(row.apply_json["version_skill_id"])["digest"], row.applied_digest)
        self.assertEqual(_digest(Path(self.fixture["target_path"])), self.fixture["skill_digest"])
        repeated = self.client.post(endpoint, json=payload)
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assert_formal_unchanged(switched=True)

    def test_restart_recovers_unfinished_swap_without_model_or_losing_original(self):
        from creatoros.integrations.skill_calibration_apply import _directory
        calibration = self.trial(self.create())
        row = self.service._row(calibration["id"])
        payload = self.apply_payload(calibration)
        request_key = signature({"request_id": payload["request_id"]})
        directory = _directory(self.service, row.id, request_key)
        candidate = self.extractions._draft_root(calibration["job"]) / "mind"
        freeze_skill(candidate, directory / "candidate", _digest(candidate))
        receipt = {"request_id": payload["request_id"], "signature": signature(payload),
            "request_key": request_key, "draft_digest": payload["expected_digest"],
            "trial_id": payload["trial_id"], "candidate_digest": _digest(candidate), "phase": "prepared"}
        self.service._update(row.id, status="applying", apply_json=receipt)
        Path(self.fixture["target_path"]).rename(directory / "backup")
        with patch.object(self.extractions, "reviser", side_effect=AssertionError("must not call SDK")):
            self.service.start()
        self.assertEqual(self.service._row(row.id).status, "draft")
        self.assertEqual(self.service._row(row.id).apply_json["phase"], "rolled_back")
        self.assert_formal_unchanged()

    def test_restart_confirms_published_files_and_preserves_previous_version(self):
        from creatoros.integrations.skill_calibration_apply import _directory
        calibration = self.trial(self.create())
        row = self.service._row(calibration["id"])
        payload = self.apply_payload(calibration)
        request_key = signature({"request_id": payload["request_id"]})
        directory = _directory(self.service, row.id, request_key)
        candidate = self.extractions._draft_root(calibration["job"]) / "mind"
        freeze_skill(candidate, directory / "candidate", _digest(candidate))
        receipt = {"request_id": payload["request_id"], "signature": signature(payload),
            "request_key": request_key, "draft_digest": payload["expected_digest"],
            "trial_id": payload["trial_id"], "candidate_digest": _digest(candidate), "phase": "prepared"}
        self.service._update(row.id, status="applying", apply_json=receipt)
        target = Path(self.fixture["target_path"])
        target.rename(directory / "backup")
        (directory / "candidate").rename(target)
        self.service.start()
        self.assertEqual(self.service._row(row.id).status, "applied")
        self.assertEqual(self.service._row(row.id).applied_digest, _digest(target))
        self.assertEqual(tree_hashes(directory / "backup"), self.fixture["baseline_files"])
        self.assertEqual(self.service.get(row.id)["job"]["status"], "saved")
        self.assert_formal_unchanged(target=False)

    def test_reader_and_freeze_wait_for_shared_directory_publication_gap(self):
        from creatoros.integrations.producer_skills import _SKILL_EDIT_LOCK
        source = Path(self.fixture["target_path"])
        staging, backup = self.root / "replacement", self.root / "previous"
        freeze_skill(source, staging, _digest(source))
        path = staging / "SKILL.md"
        path.write_text(path.read_text(encoding="utf-8") + "\n并发发布的新版本\n", encoding="utf-8")
        new_digest = _digest(staging)
        gap, release, freeze_done, read_done = [threading.Event() for _ in range(4)]
        failures = []
        def publish():
            with _SKILL_EDIT_LOCK:
                source.rename(backup)
                gap.set()
                release.wait(5)
                staging.rename(source)
        def freeze():
            try:
                freeze_skill(source, self.root / "new-snapshot", new_digest)
            except Exception as error:
                failures.append(error)
            finally:
                freeze_done.set()
        def read():
            try:
                self.catalog.describe(self.fixture["skill_id"])
            except Exception as error:
                failures.append(error)
            finally:
                read_done.set()
        publisher = threading.Thread(target=publish)
        readers = [threading.Thread(target=freeze), threading.Thread(target=read)]
        publisher.start()
        self.assertTrue(gap.wait(5))
        try:
            for reader in readers:
                reader.start()
            self.assertFalse(freeze_done.wait(.1))
            self.assertFalse(read_done.is_set())
        finally:
            release.set()
            publisher.join(5)
            for reader in readers:
                reader.join(5)
        self.assertEqual(failures, [])
        self.assertEqual(_digest(self.root / "new-snapshot"), new_digest)
        self.assertEqual(tree_hashes(backup), self.fixture["baseline_files"])

    def test_actual_progress_datetime_serializes_in_all_http_projections(self):
        from datetime import datetime, timezone
        calibration = self.create()
        original = self.extractions.get
        def real_progress_shape(identifier):
            job = original(identifier)
            job["progress"] = {"last_activity_at": datetime.now(timezone.utc)}
            return job
        with patch.object(self.extractions, "get", real_progress_shape):
            for url in ("/api/skill-calibrations", "/api/skill-calibrations/" + calibration["id"]):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                item = result["items"][0] if "items" in result else result
                self.assertIsInstance(item["job"]["progress"]["last_activity_at"], str)

    def test_reference_only_calibration_makes_empty_series_archive_not_fk_failure(self):
        series_id = "empty-calibration-series"
        f = self.fixture
        with self.db.session() as session:
            session.add(Series(id=series_id, creator_id=f["creator_id"], name="只有校准历史", description="", audience="",
                skill_name=None, mind_skill_id=f["skill_id"], production_skill_id=f["companion_id"]))
        calibration = self.create(self.payload(series_id=series_id, artifacts=[]))
        response = self.client.request("DELETE", "/api/series/" + series_id,
            json={"request_id": uuid4().hex, "expected_revision": 1})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "archived")
        with self.db.session() as session:
            self.assertFalse(session.get(Series, series_id).is_active)
            self.assertIsNotNone(session.get(SkillCalibration, calibration["id"]))
        self.assertEqual(self.service.get(calibration["id"])["id"], calibration["id"])


if __name__ == "__main__":
    unittest.main()
