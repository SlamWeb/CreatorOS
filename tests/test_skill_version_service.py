"""Version lifecycle over real isolated files/SQLite with a controlled producer.

ControlledCalibrationProducer makes deterministic native artifacts to validate
recovery and acceptance gates; this is not a model-quality or paid E2E result.
"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import event, select

from creatoros.integrations.producer_skills import ProducerSkillCatalog, SkillInstallService, skills_root_for
from creatoros.integrations.skill_extraction import ExtractionError, SkillExtractionService
from creatoros.runs import ContentRunService
from creatoros.storage import ContentRepository, ContentRevision, ContentRun, Database, Series, TopicSource, WriteReceipt, upgrade_database
from creatoros.web import create_app
from creatoros.web.account_context import CreatorContextBuilder
from tests.skill_calibration_fixture import protected_records, seed_calibration, tree_hashes


class SkillVersionServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        url = f"sqlite:///{(self.root / 'test.db').as_posix()}"
        upgrade_database(url)
        self.db = Database(url)
        self.addCleanup(self.db.close)
        self.catalog = ProducerSkillCatalog(skills_root_for(self.db))
        self.extractions = SkillExtractionService(self.catalog)
        self.runs = ContentRunService(self.db, output_root=self.root / "outputs")
        self.app = create_app(database=self.db, run_service=self.runs,
            skill_install_service=SkillInstallService(self.catalog), skill_extraction_service=self.extractions)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.fixture = seed_calibration(self.app, self.root, multi_revision=True)
        self.service = self.app.state.skill_versions
        self.baseline = protected_records(self.db)
        self.artifacts = tree_hashes(self.runs.output_root)
        self.target = tree_hashes(self.catalog.locate(self.fixture["skill_id"]))
        self.companion = tree_hashes(self.catalog.locate(self.fixture["companion_id"]))

    def candidate(self, skill_id=None):
        identity = skill_id or self.fixture["skill_id"]
        source = self.catalog.read_skill_file(identity, "SKILL.md")
        response = self.client.put(f"/api/producer-skills/{identity}/files/content", json={
            "path": "SKILL.md", "content": source["content"] + "\n用具体小事收尾，不空讲道理。\n",
            "expected_digest": source["digest"]})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def trial(self, candidate, series_id=None, request_id=None):
        response = self.client.post(f"/api/producer-skills/{candidate['skill_id']}/version-trials", json={
            "request_id": request_id or uuid4().hex, "expected_digest": candidate["digest"],
            "topic": "开头的小蛋糕在结尾分享给朋友", "series_id": series_id or self.fixture["series_id"]})
        self.assertEqual(response.status_code, 200, response.text)
        identifier = response.json()["id"]
        self.extractions.worker.join(10)
        result = self.client.get(f"/api/skill-extractions/{identifier}")
        self.assertEqual(result.status_code, 200, result.text)
        job = result.json()
        self.assertEqual(job["trials"][-1]["status"], "completed", job)
        self.assertTrue(job["trials"][-1]["thread_id"])
        self.assertTrue(job["trials"][-1]["artifact_digest"])
        return job

    def switch_payload(self, candidate, job=None, revision=1, request_id=None):
        value = dict(skill_id=candidate["skill_id"], expected_revision=revision,
                     request_id=request_id or uuid4().hex)
        if candidate.get("digest"):
            value["expected_digest"] = candidate["digest"]
        if job:
            value.update(trial_job_id=job["id"], trial_id=job["trials"][-1]["id"])
        return value

    def switch(self, payload):
        return self.client.post(f"/api/series/{self.fixture['series_id']}/skill-version", json=payload)

    def assert_original_protected(self):
        current = protected_records(self.db, self.baseline)
        for table, original in self.baseline.items():
            if table != "series":
                self.assertEqual(current[table], original, table)
        self.assertEqual(tree_hashes(self.runs.output_root), self.artifacts)
        self.assertEqual(tree_hashes(self.catalog.locate(self.fixture["skill_id"])), self.target)
        self.assertEqual(tree_hashes(self.catalog.locate(self.fixture["companion_id"])), self.companion)

    def alternative_companion(self):
        source = self.root / ("alternative-" + uuid4().hex)
        source.mkdir()
        (source / "SKILL.md").write_text(
            "---\nname: alternative-visual\ndescription: 不同的呈现搭档\n"
            "creatoros-output: social-content-pack.image-carousel\n---\n制作一张图片。\n", encoding="utf-8")
        return self.catalog.register_local(source, role="production")

    def publish(self, candidate, job=None):
        payload = {"expected_digest": candidate["digest"], "request_id": uuid4().hex}
        if job:
            payload.update(trial_job_id=job["id"], trial_id=job["trials"][-1]["id"])
        response = self.client.post(f"/api/producer-skills/{candidate['skill_id']}/publish-version", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_trial_stale_edit_then_new_trial_activate_and_rollback(self):
        candidate = self.candidate()
        first = self.trial(candidate)
        changed = self.client.put(f"/api/producer-skills/{candidate['skill_id']}/files/content", json={
            "path": "SKILL.md", "content": candidate["content"] + "\n保留开场道具。\n",
            "expected_digest": candidate["digest"]})
        self.assertEqual(changed.status_code, 200, changed.text)
        candidate = changed.json()
        with self.assertRaises(ExtractionError):
            self.service.checked_trial(candidate["skill_id"], candidate["digest"],
                                       first["id"], first["trials"][-1]["id"])
        second = self.trial(candidate)
        self.assertNotEqual(first["trials"][-1]["thread_id"], second["trials"][-1]["thread_id"])
        payload = self.switch_payload(candidate, second)
        activated = self.switch(payload)
        self.assertEqual(activated.status_code, 200, activated.text)
        self.assertEqual(activated.json()["revision"], 2)
        self.assertEqual(self.switch(payload).json(), activated.json())
        tampered = self.switch({**payload, "skill_id": self.fixture["skill_id"]})
        self.assertEqual(tampered.status_code, 409)
        rollback = self.switch(self.switch_payload({"skill_id": self.fixture["skill_id"]}, revision=2))
        self.assertEqual(rollback.status_code, 200, rollback.text)
        with self.db.session() as db:
            column = db.get(Series, self.fixture["series_id"])
            self.assertEqual(column.mind_skill_id, self.fixture["skill_id"])
            self.assertEqual(column.revision, 3)
            receipts = list(db.scalars(select(WriteReceipt)))
            self.assertEqual(len(receipts), 2)
        self.assert_original_protected()

    def test_explicit_trial_validation_rejects_changed_artifact(self):
        candidate = self.candidate()
        job = self.trial(candidate)
        from creatoros.integrations.skill_workbench import trial_directory
        directory = trial_directory(self.extractions, job["id"], job["trials"][-1]["id"])
        image = next((directory / "images").glob("*.png"))
        Image.new("RGB", (20, 20), "red").save(image)
        with self.assertRaises((ExtractionError, ValueError)):
            self.service.checked_trial(candidate["skill_id"], candidate["digest"],
                                       job["id"], job["trials"][-1]["id"])
        self.assertEqual(self.catalog.describe(candidate["skill_id"])["version_status"], "candidate")
        self.assert_original_protected()

    def test_old_queued_run_keeps_exact_input_and_new_run_uses_selected_version(self):
        repo = ContentRepository(self.db)
        old_topic, new_topic = "old-queued-" + uuid4().hex, "new-queued-" + uuid4().hex
        repo.add_topic(topic_id=old_topic, series_id=self.fixture["series_id"], title="切换前排队的选题",
                       source=TopicSource.MANUAL)
        old_run = self.runs.create(old_topic)
        with self.db.session() as db:
            old_snapshot = db.get(ContentRun, old_run.id).input_snapshot_json
            old_revision = db.scalar(select(ContentRevision).where(ContentRevision.content_run_id == old_run.id))
            frozen_revision = old_revision.production_input_json
        self.baseline = protected_records(self.db)
        candidate = self.candidate()
        job = self.trial(candidate)
        activated = self.switch(self.switch_payload(candidate, job))
        self.assertEqual(activated.status_code, 200, activated.text)
        repo.add_topic(topic_id=new_topic, series_id=self.fixture["series_id"], title="切换后新建的选题",
                       source=TopicSource.MANUAL)
        new_run = self.runs.create(new_topic)
        with self.db.session() as db:
            old = db.get(ContentRun, old_run.id)
            new = db.get(ContentRun, new_run.id)
            revision = db.scalar(select(ContentRevision).where(ContentRevision.content_run_id == old_run.id))
            self.assertEqual(old.input_snapshot_json, old_snapshot)
            self.assertEqual(revision.production_input_json, frozen_revision)
            self.assertEqual(old.input_snapshot_json["composition"]["mind"]["id"], self.fixture["skill_id"])
            self.assertEqual(new.input_snapshot_json["composition"]["mind"]["id"], candidate["skill_id"])
            self.assertEqual(new.input_snapshot_json["composition"]["mind"]["digest"], candidate["digest"])
            self.assertEqual(old.status.value, "queued")
            self.assertEqual(new.status.value, "queued")
        self.assert_original_protected()

    def test_explicit_trial_validation_rejects_column_change_and_switch_keeps_revision_cas(self):
        candidate = self.candidate()
        job = self.trial(candidate)
        with self.db.session() as db:
            column = db.get(Series, self.fixture["series_id"])
            column.description = "试用后的真实栏目配置变更"
            db.flush()
            changed_revision = column.revision
        with self.db.session() as db:
            column = db.get(Series, self.fixture["series_id"])
            with self.assertRaises(ExtractionError):
                self.service.checked_trial(candidate["skill_id"], candidate["digest"],
                    job["id"], job["trials"][-1]["id"], series=column)
        rejected = self.switch(self.switch_payload(candidate, revision=1))
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertGreater(changed_revision, 1)
        self.assert_original_protected()

    def test_committed_receipt_replays_after_candidate_was_published(self):
        candidate = self.candidate()
        job = self.trial(candidate)
        request = self.switch_payload(candidate, job)
        first = self.switch(request)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(self.catalog.describe(candidate["skill_id"])["version_status"], "released")
        second = self.switch(request)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(len(self.extractions.get(job["id"])["trials"]), 1)
        self.assert_original_protected()

    def test_sqlite_commit_failure_keeps_binding_and_same_request_can_recover(self):
        candidate = self.candidate()
        job = self.trial(candidate)
        request = self.switch_payload(candidate, job)
        def fail_version_commit(session):
            if any(isinstance(row, WriteReceipt) and row.response_json.get("kind") == "skill_version"
                   for row in session.new):
                raise RuntimeError("controlled commit failure")
        session_type = self.db._session_factory.class_
        event.listen(session_type, "before_commit", fail_version_commit)
        try:
            with self.assertRaisesRegex(RuntimeError, "controlled commit failure"):
                self.service.switch(self.fixture["series_id"], **request)
        finally:
            event.remove(session_type, "before_commit", fail_version_commit)
        with self.db.session() as db:
            column = db.get(Series, self.fixture["series_id"])
            self.assertEqual(column.mind_skill_id, self.fixture["skill_id"])
            self.assertEqual(column.revision, 1)
            self.assertEqual(db.query(WriteReceipt).count(), 0)
        retry = self.switch(request)
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.json()["revision"], 2)
        with self.db.session() as db:
            self.assertEqual(db.query(WriteReceipt).count(), 1)
        self.assertEqual(len(self.extractions.get(job["id"])["trials"]), 1)
        self.assert_original_protected()

    def test_publish_without_trial_then_first_activation_and_rollback_does_not_generate(self):
        candidate = self.candidate()
        jobs_before = set((self.extractions.root / "jobs").glob("*/job.json"))
        published = self.publish(candidate)
        self.assertEqual(published["version_status"], "released")
        self.assertEqual(self.publish(candidate)["digest"], candidate["digest"])
        with self.db.session() as db:
            self.assertEqual(db.get(Series, self.fixture["series_id"]).mind_skill_id, self.fixture["skill_id"])
        activated = self.switch(self.switch_payload(candidate))
        self.assertEqual(activated.status_code, 200, activated.text)
        rollback = self.switch(self.switch_payload({"skill_id": self.fixture["skill_id"]}, revision=2))
        self.assertEqual(rollback.status_code, 200, rollback.text)
        with self.db.session() as db:
            column = db.get(Series, self.fixture["series_id"])
            self.assertEqual(column.mind_skill_id, self.fixture["skill_id"])
            self.assertEqual(column.revision, 3)
        self.assertEqual(set((self.extractions.root / "jobs").glob("*/job.json")), jobs_before)
        self.assert_original_protected()

    def test_human_switch_can_keep_candidate_without_trial_and_legacy_fields_are_optional(self):
        candidate = self.candidate()
        payload = self.switch_payload(candidate)
        with patch.object(self.service, "checked_trial", side_effect=AssertionError("no implicit paid gate")):
            switched = self.switch(payload)
            self.assertEqual(switched.status_code, 200, switched.text)
            self.assertEqual(self.switch(payload).json(), switched.json())
        self.assertEqual(self.catalog.describe(candidate["skill_id"])["version_status"], "released")
        self.assertEqual(list((self.extractions.root / "jobs").glob("*/job.json")), [])
        self.assert_original_protected()

    def test_publish_and_switch_reject_stale_digest_without_touching_binding_or_files(self):
        candidate = self.candidate()
        changed = self.catalog.update_skill_file(candidate["skill_id"], "SKILL.md",
            candidate["content"] + "\n人工补充一处细节。\n", candidate["digest"])
        candidate_bytes = tree_hashes(self.catalog.locate(candidate["skill_id"]))
        published = self.client.post(f"/api/producer-skills/{candidate['skill_id']}/publish-version", json={
            "expected_digest": candidate["digest"], "request_id": uuid4().hex})
        self.assertEqual(published.status_code, 409, published.text)
        stale_switch = self.switch(self.switch_payload(candidate))
        self.assertEqual(stale_switch.status_code, 409, stale_switch.text)
        with self.db.session() as db:
            self.assertEqual(db.get(Series, self.fixture["series_id"]).revision, 1)
            self.assertEqual(db.query(WriteReceipt).count(), 0)
        self.assertEqual(self.catalog.describe(candidate["skill_id"])["version_status"], "candidate")
        self.assertEqual(tree_hashes(self.catalog.locate(candidate["skill_id"])), candidate_bytes)
        self.assertNotEqual(changed["digest"], candidate["digest"])
        self.assert_original_protected()

    def test_unused_legacy_trial_fields_do_not_gate_publish_or_switch(self):
        candidate = self.candidate()
        with patch.object(self.service, "checked_trial", side_effect=AssertionError("legacy hints are not authority")):
            published = self.client.post(f"/api/producer-skills/{candidate['skill_id']}/publish-version", json={
                "expected_digest": candidate["digest"], "request_id": uuid4().hex,
                "trial_job_id": "a" * 64, "trial_id": "b" * 64})
            self.assertEqual(published.status_code, 200, published.text)
            switched = self.switch({**self.switch_payload(candidate),
                                   "trial_job_id": "a" * 64, "trial_id": "b" * 64})
            self.assertEqual(switched.status_code, 200, switched.text)
        self.assertEqual(list((self.extractions.root / "jobs").glob("*/job.json")), [])
        self.assert_original_protected()

    def test_saving_version_does_not_remove_family_binding_boundary(self):
        candidate = self.candidate()
        self.publish(candidate)
        unrelated = self.alternative_companion()
        rejected = self.switch(self.switch_payload({"skill_id": unrelated["id"], "digest": unrelated["digest"]}))
        self.assertEqual(rejected.status_code, 409, rejected.text)
        with self.db.session() as db:
            self.assertEqual(db.get(Series, self.fixture["series_id"]).mind_skill_id, self.fixture["skill_id"])
            self.assertEqual(db.get(Series, self.fixture["series_id"]).revision, 1)
        self.assert_original_protected()

    def test_activation_changes_only_selected_column_and_other_column_may_explicitly_select(self):
        candidate = self.candidate()
        activated = self.switch(self.switch_payload(candidate))
        self.assertEqual(activated.status_code, 200, activated.text)
        alternate = self.alternative_companion()
        other_id = "another-series-" + uuid4().hex
        with self.db.session() as db:
            db.add(Series(id=other_id, creator_id=self.fixture["creator_id"], name="不同搭档的栏目",
                description="", audience="", skill_name=None, mind_skill_id=self.fixture["skill_id"],
                production_skill_id=alternate["id"]))
        request = self.switch_payload(candidate)
        with self.db.session() as db:
            self.assertEqual(db.get(Series, other_id).mind_skill_id, self.fixture["skill_id"])
            original_revision = db.get(Series, self.fixture["series_id"]).revision
        selected = self.client.post(f"/api/series/{other_id}/skill-version", json=request)
        self.assertEqual(selected.status_code, 200, selected.text)
        with self.db.session() as db:
            self.assertEqual(db.get(Series, other_id).mind_skill_id, candidate["skill_id"])
            self.assertEqual(db.get(Series, self.fixture["series_id"]).revision, original_revision)
        self.assert_original_protected()

    def test_normally_bound_published_version_remains_a_rollback_target(self):
        # A complete Visualize can be tried before any column uses this edition.
        v2 = self.candidate(self.fixture["companion_id"])
        unbound = self.client.post(f"/api/producer-skills/{v2['skill_id']}/version-trials", json={
            "expected_digest": v2["digest"], "request_id": uuid4().hex,
            "topic": "先单独试用呈现方法，再用于新栏目"})
        self.assertEqual(unbound.status_code, 200, unbound.text)
        self.extractions.worker.join(10)
        j2 = self.extractions.get(unbound.json()["id"])
        self.assertEqual(j2["trials"][-1]["status"], "completed", j2)
        self.assertIsNone(j2["version_series_id"])
        self.publish(v2, j2)
        created = self.client.post("/api/series", json={"name": "从已保存v2创建的新栏目",
            "creator_id": self.fixture["creator_id"], "mind_skill_id": self.fixture["skill_id"],
            "production_skill_id": v2["skill_id"], "request_id": uuid4().hex})
        self.assertEqual(created.status_code, 201, created.text)
        series_id = created.json()["series"]["id"]
        self.assertEqual(created.json()["series"]["revision"], 1)
        v3 = self.candidate(v2["skill_id"])
        j3 = self.trial(v3, series_id=series_id)
        endpoint = f"/api/series/{series_id}/skill-version"
        switched = self.client.post(endpoint, json=self.switch_payload(v3, j3))
        self.assertEqual(switched.status_code, 200, switched.text)
        # Saving/rebinding this edition elsewhere does not mutate the original column.
        with self.db.session() as db:
            self.assertEqual(db.get(Series, self.fixture["series_id"]).production_skill_id,
                             self.fixture["companion_id"])
        rollback = self.client.post(endpoint, json=self.switch_payload(v2, revision=2))
        self.assertEqual(rollback.status_code, 200, rollback.text)
        with self.db.session() as db:
            column = db.get(Series, series_id)
            self.assertEqual(column.production_skill_id, v2["skill_id"])
            self.assertEqual(column.revision, 3)
            original = db.get(Series, self.fixture["series_id"])
            self.assertEqual(original.production_skill_id, self.fixture["companion_id"])
            self.assertEqual(original.revision, 1)
        self.assert_original_protected()

    def test_failed_switch_release_preserves_revision_cas_after_companion_change(self):
        candidate = self.candidate()
        job = self.trial(candidate)
        request = self.switch_payload(candidate, job)
        def fail_version_commit(session):
            if any(isinstance(row, WriteReceipt) and row.response_json.get("kind") == "skill_version"
                   for row in session.new):
                raise RuntimeError("controlled commit failure")
        session_type = self.db._session_factory.class_
        event.listen(session_type, "before_commit", fail_version_commit)
        try:
            with self.assertRaisesRegex(RuntimeError, "controlled commit failure"):
                self.service.switch(self.fixture["series_id"], **request)
        finally:
            event.remove(session_type, "before_commit", fail_version_commit)
        alternate = self.alternative_companion()
        with self.db.session() as db:
            column = db.get(Series, self.fixture["series_id"])
            column.production_skill_id = alternate["id"]
            db.flush()
            revision = column.revision
        stale = self.switch(request)
        self.assertEqual(stale.status_code, 409, stale.text)
        with self.db.session() as db:
            self.assertEqual(db.get(Series, self.fixture["series_id"]).mind_skill_id, self.fixture["skill_id"])
            self.assertEqual(db.query(WriteReceipt).count(), 0)
        # A fresh explicit human confirmation may use the saved edition with
        # the latest companion; it does not need another paid trial.
        confirmed = self.switch(self.switch_payload(candidate, revision=revision))
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assert_original_protected()

    def test_account_context_keeps_exact_older_binding_after_new_version_release(self):
        candidate = self.candidate()
        job = self.trial(candidate)
        self.publish(candidate, job)
        self.assertNotIn(self.fixture["skill_id"], {item["id"] for item in self.catalog.list()})
        context = CreatorContextBuilder(self.db, self.catalog).build(self.fixture["creator_id"])
        column = next(item for item in context["series"] if item["id"] == self.fixture["series_id"])
        self.assertEqual(column["skill_bindings"]["mind"], self.fixture["skill_id"])
        metadata = next(item for item in context["skills"] if item["id"] == self.fixture["skill_id"])
        self.assertTrue(metadata["available"])
        detail = self.app.state.queries.get_series(self.fixture["series_id"])
        self.assertEqual(detail.mind_skill_id, self.fixture["skill_id"])
        self.assert_original_protected()

    def test_unrelated_missing_skill_does_not_break_healthy_version_history(self):
        candidate = self.candidate()
        pointer = self.catalog.root / "version-trials" / (candidate["skill_id"] + ".json")
        pointer.parent.mkdir(exist_ok=True)
        pointer.write_text("interrupted index write", encoding="utf-8")
        with self.db.session() as db:
            db.add(Series(id="broken-unrelated", creator_id=self.fixture["creator_id"], name="旧的无效绑定",
                description="", audience="", skill_name="missing-library-item--0000000000000000"))
        response = self.client.get(f"/api/producer-skills/{candidate['skill_id']}/versions")
        self.assertEqual(response.status_code, 200, response.text)
        history = response.json()
        self.assertEqual({item["id"] for item in history["items"]},
                         {self.fixture["skill_id"], candidate["skill_id"]})
        self.assertEqual({item["series_id"] for item in history["bindings"]}, {self.fixture["series_id"]})
        damaged_index = next(item for item in history["items"] if item["id"] == candidate["skill_id"])
        self.assertTrue(damaged_index["version_trial_error"])
        self.assert_original_protected()

    def test_trial_inherits_acl_before_visibility_and_observation_reads_short_path(self):
        from creatoros.integrations.producer_skills import inherit_copy_permissions
        from creatoros.integrations.skill_workbench import trial_directory
        candidate = self.candidate()
        observed = []
        def inherit_before_publish(staging):
            self.assertTrue((staging / "job.json").is_file())
            job = json.loads((staging / "job.json").read_text(encoding="utf-8"))
            self.assertFalse(self.extractions._path("jobs", job["id"]).exists())
            inherit_copy_permissions(staging)
            observed.append(job["id"])
        with patch("creatoros.integrations.skill_version_service.inherit_copy_permissions", inherit_before_publish):
            job = self.trial(candidate)
        self.assertEqual(observed, [job["id"]])
        trial = job["trials"][-1]
        refs = ["extraction_execution", job["id"], "trial", trial["id"]]
        observation = self.app.state.observation
        _root, directory, thread = observation._worker_location(refs)
        self.assertEqual(directory, trial_directory(self.extractions, job["id"], trial["id"]))
        self.assertIn(trial["id"][:16], directory.parts)
        self.assertNotIn(trial["id"], directory.parts)
        self.assertEqual(thread, trial["thread_id"])
        host = observation._worker_host(refs)
        self.assertTrue(host["current_execution"])
        self.assertFalse(host["active"])
        self.assert_original_protected()

    def test_version_history_cannot_be_removed_via_old_delete_endpoint(self):
        candidate = self.candidate()
        for identity in (candidate["skill_id"], self.fixture["skill_id"]):
            item = self.catalog.describe(identity)
            response = self.client.request("DELETE", f"/api/producer-skills/{identity}",
                                           json={"expected_digest": item["digest"]})
            self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(len(self.catalog.list_skill_versions(candidate["skill_id"])["items"]), 2)
        self.assert_original_protected()


if __name__ == "__main__":
    unittest.main()
