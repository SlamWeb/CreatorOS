"""Isolated fault injection for received replies, billing and stage recovery.

No paid APIs or formal data: deterministic provider replies deliberately break
JSON/semantic contracts. Real frontend/model acceptance is recorded separately.
"""
from copy import deepcopy
import json
from threading import Event, Thread
import unittest
from unittest.mock import patch

from creatoros.ai.types import ModelResponse, ModelUsage
from creatoros.integrations.skill_iteration_judge import ReplyFormatError, evaluate, parse_reply
from creatoros.integrations.skill_iterations import SkillIterationService
from creatoros.integrations.skill_extraction import ExtractionError
from creatoros.storage import ContentRepository, ContentRun, ContentRunStatus, Series, SkillIteration, Topic
from creatoros.storage.models import ProductionQueueItem
from tests import test_skill_iteration_service as fixtures


def valid_reply():
    return dict(scores=[dict(criterion_id=f"c{index}", score=90, evidence="受控试产第1页的可检查位置。")
                        for index in range(1, 4)], feedback="受控格式恢复；不是质量收益。", new_criteria=[])


class ScriptedProvider:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def __call__(self):
        parent = self
        class Provider:
            def complete(self, context):
                parent.calls.append(context)
                reply = parent.responses.pop(0)
                if callable(reply):
                    reply = reply()
                if isinstance(reply, Exception):
                    raise reply
                return ModelResponse(reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False),
                                     [], ModelUsage(103, 19, 122))
        return Provider()


class SkillIterationJudgeRecoveryTests(unittest.TestCase):
    setUp = fixtures.SkillIterationServiceTests.setUp
    create = fixtures.SkillIterationServiceTests.create
    drive = fixtures.SkillIterationServiceTests.drive
    assert_originals = fixtures.SkillIterationServiceTests.assert_originals

    def at_judge(self):
        item = self.create()
        current = self.drive(item["id"], until=lambda item: item["stage"] == "judge")
        self.assertEqual(current["stage"], "judge", current)
        self.assertEqual(len(self.producer.calls), 1)
        return current

    def use_replies(self, *responses):
        provider = ScriptedProvider(responses)
        self.iterations.evaluator = evaluate
        self.iterations.provider_factory = provider
        return provider

    def test_invalid_json_is_saved_and_billed_before_one_format_retry_without_redrawing(self):
        item = self.at_judge()
        provider = self.use_replies("{invalid json", valid_reply())
        observed = []

        def verify_order(kind, raw, payload=None):
            number = len(provider.calls)
            directory = self.iterations._path(item["id"]) / f"model/judge-1-1-{number}"
            self.assertTrue((directory / "raw-response.json").is_file())
            state = self.iterations._row(item["id"]).state_json
            self.assertEqual(state["usage"]["input_tokens"], 100 + 103 * number)
            observed.append(number)
            return parse_reply(kind, raw, payload)

        with patch("creatoros.integrations.skill_iteration_judge.parse_reply", verify_order):
            done = self.drive(item["id"])
        self.assertEqual(done["status"], "completed", done)
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(len(self.producer.calls), 1)
        self.assertEqual(done["round_count"], 1)
        self.assertEqual(done["max_rounds"], 3)
        self.assertEqual(done["usage"], dict(input_tokens=306, output_tokens=58, calls=3))
        call = done["model_calls"]["judge-1-1"]
        self.assertEqual([a["status"] for a in call["attempts"]], ["invalid_response", "completed"])
        self.assertTrue(all(a["usage_accounted"] for a in call["attempts"]))
        base = self.iterations._path(item["id"])
        self.assertEqual(json.loads((base / "model/judge-1-1-1/raw-response.json").read_text(encoding="utf-8"))["content"], "{invalid json")
        first = json.loads((base / "model/judge-1-1-1/request.json").read_text(encoding="utf-8"))
        second = json.loads((base / "model/judge-1-1-2/request.json").read_text(encoding="utf-8"))
        self.assertEqual(first, second)
        self.assertTrue((base / "model/judge-1-1-2/repair.json").is_file())
        self.assertEqual(observed, [1, 2])
        self.assert_originals()

    def test_semantic_contract_checks_coverage_duplicates_types_and_bounds(self):
        payload = dict(criteria=[dict(id=f"c{i}", critical=True) for i in range(1, 4)], allow_additions=False)
        broken = []
        missing = valid_reply(); missing["scores"].pop(); broken.append(missing)
        duplicate = valid_reply(); duplicate["scores"][1]["criterion_id"] = "c1"; broken.append(duplicate)
        extra = valid_reply(); extra["scores"].append(dict(criterion_id="c4", score=90, evidence="extra")); broken.append(extra)
        typed = valid_reply(); typed["scores"][0]["score"] = "90"; broken.append(typed)
        high = valid_reply(); high["scores"][0]["score"] = 101; broken.append(high)
        negative = valid_reply(); negative["scores"][0]["score"] = -1; broken.append(negative)
        addition = valid_reply(); addition["new_criteria"] = [dict(description="unauthorized", evidence="none", critical=False)]; broken.append(addition)
        for result in broken:
            with self.subTest(result=result), self.assertRaises(ReplyFormatError):
                parse_reply("judge", {"content": json.dumps(result)}, payload)
        with self.assertRaises(ReplyFormatError):
            parse_reply("judge", {"content": "``````"}, payload)

    def test_semantic_failure_retries_before_cached_completed_and_leaves_failure_evidence(self):
        item = self.at_judge()
        broken = valid_reply(); broken["scores"][1]["criterion_id"] = "c1"
        provider = self.use_replies(broken, valid_reply())
        done = self.drive(item["id"])
        self.assertEqual(done["status"], "completed", done)
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(len(self.producer.calls), 1)
        self.assertEqual(done["model_calls"]["judge-1-1"]["attempts"][0]["status"], "invalid_response")
        self.assertTrue(any(event["kind"] == "model_invalid_response" for event in done["events"]))
        self.assert_originals()

    def test_exhausted_format_retry_resumes_same_judge_without_growing_rounds_or_new_inputs(self):
        item = self.at_judge()
        original = deepcopy(self.iterations._row(item["id"]).config_json)
        provider = self.use_replies("broken one", "broken two", valid_reply())
        failed = self.drive(item["id"])
        self.assertEqual(failed["status"], "failed", failed)
        self.assertEqual(failed["stage"], "judge")
        self.assertEqual(failed["allowed_actions"], ["resume"])
        self.assertEqual(failed["next_action"], "resume_current_model_stage")
        self.assertEqual(failed["usage"], dict(input_tokens=306, output_tokens=58, calls=3))
        old_files = fixtures.tree_hashes(self.iterations._path(item["id"]) / "model")
        # Isolated current source edits must not replace the frozen trial topic
        # or frozen reference/Skill input on a Judge-only recovery.
        with self.db.session() as db:
            topic = db.get(Topic, original["topic_id"])
            topic.title, topic.brief = "later title", "later brief must not leak into old RSI"
        source = self.catalog.read_skill_file(original["skill_id"], "SKILL.md")
        newer = self.catalog.update_skill_file(original["skill_id"], "SKILL.md", source["content"] + "\nNEW RULE MUST NOT LEAK\n", source["digest"])
        self.assertNotEqual(newer["skill_id"], original["skill_id"])
        resumed = self.iterations.action(item["id"], action="resume", expected_revision=failed["revision"], request_id="retry-judge")
        self.assertEqual(resumed["max_rounds"], 3)
        done = self.drive(item["id"])
        self.assertEqual(done["status"], "completed", done)
        self.assertEqual(done["round_count"], 1)
        self.assertEqual(done["max_rounds"], 3)
        self.assertEqual(len(provider.calls), 3)
        self.assertEqual(len(self.producer.calls), 1)
        self.assertEqual(self.iterations._row(item["id"]).config_json, original)
        first = json.loads((self.iterations._path(item["id"]) / "model/judge-1-1-1/request.json").read_text(encoding="utf-8"))
        third = json.loads((self.iterations._path(item["id"]) / "model/judge-1-1-3/request.json").read_text(encoding="utf-8"))
        self.assertEqual(first, third)
        after = fixtures.tree_hashes(self.iterations._path(item["id"]) / "model")
        self.assertTrue(all(after[name] == value for name, value in old_files.items()))
        self.assertEqual(fixtures.tree_hashes(self.runs.output_root), self.artifacts)

    def test_process_restart_recovers_raw_reply_and_missing_bill_without_another_request(self):
        item = self.at_judge()
        provider = self.use_replies(valid_reply())
        with patch.object(self.iterations, "_record_model_receipt", side_effect=RuntimeError("controlled loss before receipt DB commit")):
            failed = self.drive(item["id"])
        self.assertEqual(failed["status"], "failed", failed)
        self.assertEqual(failed["usage"]["calls"], 2)
        self.assertEqual(failed["usage"]["input_tokens"], 100)
        response = self.iterations._path(item["id"]) / "model/judge-1-1-1/raw-response.json"
        old_bytes = response.read_bytes()
        restarted = SkillIterationService(self.db, self.app.state.skill_versions, self.app.state.chat,
            evaluator=evaluate, provider_factory=provider)
        self.iterations = restarted
        # A fresh host does not automatically spend on a failed task.
        restarted.start(); restarted.shutdown(); restarted.stopping.clear()
        self.assertEqual(len(provider.calls), 1)
        state = restarted.get(item["id"])
        restarted.action(item["id"], action="resume", expected_revision=state["revision"], request_id="restart-judge")
        done = self.drive(item["id"])
        self.assertEqual(done["status"], "completed", done)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(len(self.producer.calls), 1)
        self.assertEqual(done["usage"], dict(input_tokens=203, output_tokens=39, calls=2))
        self.assertEqual(response.read_bytes(), old_bytes)
        self.assertTrue(any(event["kind"] == "model_recovered" for event in done["events"]))
        self.assert_originals()

    def test_legacy_completed_but_invalid_judge_is_not_cached_forever_or_billed_twice(self):
        item = self.at_judge()
        broken = valid_reply(); broken["scores"].pop()
        with self.db.session() as db:
            row = db.get(SkillIteration, item["id"])
            state = deepcopy(row.state_json)
            state["calls"]["judge-1-1"] = dict(status="completed", kind="judge", attempt=1,
                owner="old-host", result=broken, usage=dict(input_tokens=103, output_tokens=19))
            state["usage"].update(input_tokens=203, output_tokens=39, calls=2)
            row.status, row.last_error, row.state_json = "failed", "legacy semantic validation failed", state
        old = self.iterations.get(item["id"])
        self.iterations.action(item["id"], action="resume", expected_revision=old["revision"], request_id="legacy-semantic")
        provider = self.use_replies(valid_reply())
        done = self.drive(item["id"])
        self.assertEqual(done["status"], "completed", done)
        self.assertEqual(done["usage"], dict(input_tokens=306, output_tokens=58, calls=3))
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(len(self.producer.calls), 1)
        self.assertEqual(done["max_rounds"], 3)
        self.assert_originals()

    def test_status_explains_trial_production_and_new_request_does_not_duplicate_active_rsi(self):
        created = self.create()
        self.assertEqual(self.create()["id"], created["id"])
        with self.assertRaises(ExtractionError) as error:
            self.create(request_id="another-conversation-request")
        self.assertEqual(error.exception.status, 409)
        self.assertIn(created["id"], str(error.exception))
        self.iterations.step(created["id"])
        self.iterations.step(created["id"])
        before_submit = self.iterations.get(created["id"])
        self.assertFalse(before_submit["active_round"]["production_submitted"])
        self.assertEqual(before_submit["next_action"], "backend_submits_production")
        self.iterations.step(created["id"])
        submitted = self.iterations.get(created["id"])
        self.assertTrue(submitted["active_round"]["production_submitted"])
        self.assertEqual(submitted["next_action"], "backend_waits_for_production")
        self.assertTrue(submitted["active_round"]["trial_job_id"])
        self.assertEqual(submitted["active_round"]["trial_url"], submitted["rounds"][0]["trial_url"])
        self.extractions.worker.join(10)
        judge = self.drive(created["id"], until=lambda item: item["stage"] == "judge")
        self.assertEqual(judge["active_round"]["image_count"], 1)
        self.assertEqual(judge["next_action"], "backend_evaluates")
        self.assertEqual(len(self.producer.calls), 1)
        self.assert_originals()

    def test_ordinary_active_run_or_queue_blocks_rsi_before_freezing_or_model_calls(self):
        for status in (ContentRunStatus.PRODUCING, ContentRunStatus.VALIDATING):
            with self.subTest(status=status), self.db.session() as db:
                db.get(ContentRun, self.fixture["run_id"]).status = status
            with self.assertRaises(ExtractionError) as error:
                self.create(request_id="while-" + status.value)
            self.assertEqual(error.exception.status, 409)
        with self.db.session() as db:
            run = db.get(ContentRun, self.fixture["run_id"])
            run.status = ContentRunStatus.QUEUED
            revision = self.runs.get_active_revision(run.id)
            queued = ProductionQueueItem(run_id=run.id, revision_id=revision.id,
                                         expected_version=run.version, status="pending")
            db.add(queued)
        for status in ("pending", "running"):
            with self.subTest(status=status), self.db.session() as db:
                db.query(ProductionQueueItem).filter_by(run_id=self.fixture["run_id"]).one().status = status
            with self.assertRaises(ExtractionError):
                self.create(request_id="while-queue-" + status)
        self.assertEqual(self.evaluator.calls, [])
        self.assertEqual(self.producer.calls, [])
        self.assertFalse(self.iterations.root.exists())

    def test_ordinary_queued_run_without_execution_authorization_does_not_block_rsi(self):
        with self.db.session() as db:
            db.get(ContentRun, self.fixture["run_id"]).status = ContentRunStatus.QUEUED
        created = self.create()
        self.assertEqual(created["status"], "queued")
        self.assertEqual(self.evaluator.calls, [])
        self.assertEqual(self.producer.calls, [])

    def test_resume_and_new_trial_recheck_ordinary_production_without_scheduling_images(self):
        item = self.create()
        paused = self.iterations.action(item["id"], action="pause", expected_revision=item["revision"], request_id="before-any-model")
        with self.db.session() as db:
            db.get(ContentRun, self.fixture["run_id"]).status = ContentRunStatus.PRODUCING
        with self.assertRaises(ExtractionError):
            self.iterations.action(item["id"], action="resume", expected_revision=paused["revision"], request_id="blocked-resume")
        self.assertEqual(self.iterations.get(item["id"])["status"], "paused")
        with self.db.session() as db:
            db.get(ContentRun, self.fixture["run_id"]).status = ContentRunStatus.AWAITING_APPROVAL
        self.iterations.action(item["id"], action="resume", expected_revision=paused["revision"], request_id="safe-resume")
        self.iterations.step(item["id"])
        self.iterations.step(item["id"])
        with self.db.session() as db:
            db.get(ContentRun, self.fixture["run_id"]).status = ContentRunStatus.VALIDATING
        self.iterations.step(item["id"])
        failed = self.iterations.get(item["id"])
        self.assertEqual(failed["status"], "failed", failed)
        self.assertEqual(failed["stage"], "waiting")
        self.assertFalse(failed["active_round"]["production_submitted"])
        self.assertEqual(self.producer.calls, [])

    def test_existing_trial_collects_images_without_redrawing_even_if_other_activity_appears(self):
        item = self.create()
        self.iterations.step(item["id"])
        self.iterations.step(item["id"])
        self.iterations.step(item["id"])
        self.extractions.worker.join(10)
        with self.db.session() as db:
            db.get(ContentRun, self.fixture["run_id"]).status = ContentRunStatus.PRODUCING
        self.iterations.step(item["id"])
        judge = self.iterations.get(item["id"])
        self.assertEqual(judge["stage"], "judge", judge)
        self.assertEqual(judge["active_round"]["image_count"], 1)
        self.assertEqual(len(self.producer.calls), 1)

    def test_trial_activity_hook_is_read_only_lock_free_and_conservative_on_unknown_metadata(self):
        item = self.at_judge()
        round_ = item["rounds"][0]
        job_id, trial_id = round_["trial_job_id"], round_["trial_id"]
        path = self.extractions._path("jobs", job_id) / "job.json"
        raw = path.read_bytes()
        metadata = json.loads(raw)
        class NoLock:
            def __enter__(self):
                raise AssertionError("Read-only Run hook must not acquire Skill write locks")
        with patch.object(self.iterations, "lock", NoLock()), patch.object(self.extractions, "lock", NoLock()):
            self.assertFalse(self.iterations.is_trial_active(job_id, trial_id))
            for status in ("queued", "running", "future-unknown-status"):
                changed = deepcopy(metadata)
                changed["trials"][0]["status"] = status
                with patch.object(self.extractions, "_read", return_value=changed):
                    self.assertTrue(self.iterations.is_trial_active(job_id, trial_id))
            for status in ("completed", "failed", "interrupted", "cancelled"):
                changed = deepcopy(metadata)
                changed["trials"][0]["status"] = status
                with patch.object(self.extractions, "_read", return_value=changed):
                    self.assertFalse(self.iterations.is_trial_active(job_id, trial_id))
            with patch.object(self.extractions, "_read", side_effect=OSError("locked metadata")):
                self.assertTrue(self.iterations.is_trial_active(job_id, trial_id))
            self.assertTrue(self.iterations.is_trial_active("0" * 64, trial_id))
            self.assertTrue(self.iterations.is_trial_active(job_id, "../bad"))
        self.assertEqual(path.read_bytes(), raw)
        self.assert_originals()

    def test_pretrial_publication_failure_resumes_same_submission_without_adding_rounds(self):
        item = self.create(max_rounds=1)
        self.iterations.step(item["id"])
        self.iterations.step(item["id"])
        with patch.object(self.app.state.skill_versions, "_publish_trial_directory", side_effect=PermissionError("controlled pre-queue rename denial")):
            self.iterations.step(item["id"])
        failed = self.iterations.get(item["id"])
        self.assertEqual(failed["status"], "failed", failed)
        self.assertEqual(failed["stage"], "waiting")
        self.assertEqual(failed["next_action"], "resume_current_submission")
        self.assertEqual(failed["allowed_actions"], ["resume"])
        self.assertFalse(failed["active_round"]["production_submitted"])
        self.assertEqual(self.producer.calls, [])
        self.evaluator.scores = (90,)
        resumed = self.iterations.action(item["id"], action="resume", expected_revision=failed["revision"], request_id="same-trial-after-denial")
        self.assertEqual(resumed["max_rounds"], 1)
        done = self.drive(item["id"])
        self.assertEqual(done["status"], "completed", done)
        self.assertEqual(done["round_count"], 1)
        self.assertEqual(done["max_rounds"], 1)
        self.assertEqual(len(self.producer.calls), 1)
        from creatoros.integrations.skill_version_service import signature
        expected = signature({"kind": "skill-version-trial", "request_id": f"rsi-{item['id']}-1"})
        self.assertEqual(done["active_round"]["trial_job_id"], expected)
        self.assertEqual([call["kind"] for call in self.evaluator.calls], ["rubric", "judge"])
        self.assertTrue(any(event["kind"] == "error" for event in done["events"]))
        self.assert_originals()

    def test_paused_rsi_blocks_active_or_unknown_trial_but_allows_terminal_or_no_submission(self):
        old = self.create()
        with self.db.session() as db:
            row = db.get(SkillIteration, old["id"])
            state = deepcopy(row.state_json)
            state["rounds"] = [dict(number=1, trial_job_id="1" * 64, trial_id="2" * 64)]
            row.status, row.state_json = "paused", state
        for status in ("queued", "running", "future-unknown"):
            metadata = dict(id="1" * 64, trials=[dict(id="2" * 64, status=status)])
            with self.subTest(status=status), patch.object(self.extractions, "_read", return_value=metadata):
                with self.assertRaises(ExtractionError) as error:
                    self.create(request_id="blocked-" + status)
                self.assertEqual(error.exception.status, 409)
                self.assertIn(old["id"], str(error.exception))
        with patch.object(self.extractions, "_read", side_effect=OSError("missing or unreadable trial metadata")):
            with self.assertRaises(ExtractionError):
                self.create(request_id="blocked-missing")
        for status in ("completed", "failed", "interrupted", "cancelled"):
            metadata = dict(id="1" * 64, trials=[dict(id="2" * 64, status=status)])
            with self.subTest(status=status), patch.object(self.extractions, "_read", return_value=metadata):
                created = self.create(request_id="terminal-" + status)
                self.assertNotEqual(created["id"], old["id"])
                with self.db.session() as db:
                    db.get(SkillIteration, created["id"]).status = "paused"
        with self.db.session() as db:
            row = db.get(SkillIteration, old["id"])
            state = deepcopy(row.state_json); state["rounds"] = []
            row.state_json = state
        self.assertEqual(self.create(request_id="paused-no-submission")["status"], "queued")
        self.assertEqual(self.evaluator.calls, [])
        self.assertEqual(self.producer.calls, [])
        self.assert_originals()

    def test_transferred_column_blocks_prior_active_rsi_without_exposing_its_identity(self):
        old = self.create()
        ContentRepository(self.db).create_creator(creator_id="new-scope-owner", display_name="New owner")
        with self.db.session() as db:
            db.get(Series, self.fixture["series_id"]).creator_id = "new-scope-owner"
        for status in ("queued", "paused"):
            with self.db.session() as db:
                row = db.get(SkillIteration, old["id"])
                state = deepcopy(row.state_json)
                state["rounds"] = [dict(number=1, trial_job_id="1" * 64, trial_id="2" * 64)]
                row.status, row.state_json = status, state
            with self.subTest(status=status), patch.object(self.iterations, "is_trial_active", return_value=True):
                with self.assertRaises(ExtractionError) as error:
                    self.create(request_id="new-owner-request-" + status)
                self.assertEqual(error.exception.status, 409)
                self.assertNotIn(old["id"], str(error.exception))
                self.assertNotIn("iteration=", str(error.exception))
                self.assertNotIn(self.fixture["creator_id"], str(error.exception))
        self.assertEqual(self.evaluator.calls, [])
        self.assertEqual(self.producer.calls, [])

    def test_unknown_transport_is_not_an_automatic_format_retry(self):
        item = self.at_judge()
        provider = self.use_replies(TimeoutError("controlled result unknown"), valid_reply())
        failed = self.drive(item["id"])
        self.assertEqual(failed["status"], "failed", failed)
        self.assertEqual(failed["model_calls"]["judge-1-1"]["status"], "unknown")
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(len(self.producer.calls), 1)
        self.assertEqual(failed["usage"]["input_tokens"], 100)
        resumed = self.iterations.action(item["id"], action="continue", expected_revision=failed["revision"], request_id="explicit-retry-unknown")
        self.assertEqual(resumed["max_rounds"], 3)
        done = self.drive(item["id"])
        self.assertEqual(done["status"], "completed", done)
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(len(self.producer.calls), 1)
        self.assert_originals()

    def test_pause_during_invalid_reply_does_not_launch_format_retry_until_resume(self):
        item = self.at_judge()
        entered, release = Event(), Event()
        def blocked():
            entered.set()
            if not release.wait(10):
                raise RuntimeError("controlled test was not released")
            return "bad json while paused"
        provider = self.use_replies(blocked, valid_reply())
        worker = Thread(target=self.iterations.step, args=(item["id"],))
        self.addCleanup(release.set)
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            running = self.iterations.get(item["id"])
            self.iterations.action(item["id"], action="pause", expected_revision=running["revision"], request_id="pause-judge")
        finally:
            release.set(); worker.join(10)
        self.assertFalse(worker.is_alive())
        paused = self.iterations.get(item["id"])
        self.assertEqual(paused["status"], "paused", paused)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(paused["usage"]["input_tokens"], 203)
        self.iterations.action(item["id"], action="resume", expected_revision=paused["revision"], request_id="resume-paused-judge")
        done = self.drive(item["id"])
        self.assertEqual(done["status"], "completed", done)
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(len(self.producer.calls), 1)
        self.assert_originals()


if __name__ == "__main__":
    unittest.main()
