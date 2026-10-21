"""RSI orchestration over isolated real SQLite/files and explicit model fixtures.

Scores below are scripted fault/flow inputs, never creative-quality evidence.
ControlledCalibrationProducer creates fixture images, not paid Codex images.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import monotonic, sleep
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from creatoros.integrations.producer_skills import ProducerSkillCatalog, SkillInstallService, skills_root_for
from creatoros.integrations.skill_extraction import ExtractionError, SkillExtractionService
from creatoros.runs import ContentRunService
from creatoros.storage import ContentRepository, Database, Series, SkillIteration, upgrade_database
from creatoros.web import create_app
from tests.skill_calibration_fixture import (ControlledCalibrationProducer, protected_records,
    seed_calibration, tree_hashes)


class ControlledEvaluator:
    def __init__(self, scores=(50, 90, 90), *, append_on_round=None):
        self.calls, self.images_to_round = [], {}
        self.scores, self.append_on_round = scores, append_on_round

    def __call__(self, kind, payload, images, provider_factory):
        captured = [(name, str(path), hashlib.sha256(path.read_bytes()).hexdigest()) for name, path in images]
        self.calls.append(dict(kind=kind, payload=deepcopy(payload), images=captured))
        if not images or not all(Path(path).is_file() for _, path in images):
            raise AssertionError("Every evaluation must receive real, readable frozen reference files")
        if kind == "rubric":
            result = {"criteria": [dict(description=description, evidence="受控参考第1张的可观察事实，不是质量评分。",
                critical=index == 0) for index, description in enumerate(("词义清楚", "图文对应", "动作承接"))]}
        elif kind == "judge":
            production = tuple(str(path) for name, path in images if name.startswith("当前试产"))
            if not production:
                raise AssertionError("Judge must receive actual produced image files")
            number = self.images_to_round.setdefault(production, len(self.images_to_round) + 1)
            value = self.scores[min(number - 1, len(self.scores) - 1)]
            additions = []
            if number == self.append_on_round and len(payload["criteria"]) == 3:
                additions = [dict(description="保留参考中的低对比留白", evidence="受控优秀参考第1张背景留白区域。", critical=False)]
            result = dict(scores=[dict(criterion_id=item["id"], score=value,
                evidence=f"受控第{number}轮图片输入；脚本分数不是内容质量结论。") for item in payload["criteria"]],
                feedback=f"受控第{number}轮：按输入的固定准则检查。", new_criteria=additions)
        elif kind == "revise":
            result = dict(skill_md=payload["skill_md"] + "\n本轮独立候选：用具体动作回扣开场道具。\n",
                changes="仅在独立候选增加具体动作，不覆盖共享正文或assets。")
        else:
            raise AssertionError(kind)
        return result, {"input_tokens": 100, "output_tokens": 20}


class SkillIterationServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.url = f"sqlite:///{(self.root / 'iterations.db').as_posix()}"
        upgrade_database(self.url)
        self.db = Database(self.url)
        self.addCleanup(self.db.close)
        self.catalog = ProducerSkillCatalog(skills_root_for(self.db))
        self.extractions = SkillExtractionService(self.catalog)
        self.runs = ContentRunService(self.db, output_root=self.root / "outputs")
        self.evaluator = ControlledEvaluator()
        self.app = create_app(database=self.db, run_service=self.runs,
            skill_install_service=SkillInstallService(self.catalog), skill_extraction_service=self.extractions,
            skill_iteration_evaluator=self.evaluator)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.iterations = self.app.state.skill_iterations
        self.iterations.shutdown()
        self.iterations.stopping.clear()  # Deterministic domain stepping; no RSI worker is alive.
        self.fixture = seed_calibration(self.app, self.root, multi_revision=True)
        self.producer = ControlledCalibrationProducer()
        self.extractions.producer_factory = lambda: self.producer
        self.baseline = protected_records(self.db)
        self.artifacts = tree_hashes(self.runs.output_root)
        self.target_files = tree_hashes(self.catalog.locate(self.fixture["skill_id"]))
        self.companion_files = tree_hashes(self.catalog.locate(self.fixture["companion_id"]))

    def create(self, **changes):
        inputs = dict(series_id=self.fixture["series_id"], skill_id=self.fixture["skill_id"],
            topic_id=self.runs.get(self.fixture["run_id"]).topic_id,
            instructions="保留优秀参考中的具体动作，不能覆盖共享Skill。", max_rounds=3,
            request_id="iteration-one")
        inputs.update(changes)
        return self.iterations.create(**inputs)

    def drive(self, identifier, *, until=None, limit=60):
        end = monotonic() + limit
        while monotonic() < end:
            current = self.iterations.get(identifier)
            if until and until(current):
                return current
            if current["status"] in {"completed", "exhausted", "failed", "interrupted", "paused"}:
                return current
            self.iterations.step(identifier)
            sleep(.005)
        self.fail("RSI did not reach the expected state: " + repr(self.iterations.get(identifier)))

    def assert_originals(self):
        self.assertEqual(protected_records(self.db, self.baseline), self.baseline)
        self.assertEqual(tree_hashes(self.runs.output_root), self.artifacts)
        self.assertEqual(tree_hashes(self.catalog.locate(self.fixture["skill_id"])), self.target_files)
        self.assertEqual(tree_hashes(self.catalog.locate(self.fixture["companion_id"])), self.companion_files)

    def test_baseline_revise_second_round_passes_without_switching_or_touching_old_records(self):
        created = self.create()
        done = self.drive(created["id"])
        self.assertEqual(done["status"], "completed", done)
        self.assertEqual(len(done["rounds"]), 2)
        self.assertEqual(done["best_round"], 2)
        self.assertEqual(done["best_score"], 90)
        self.assertEqual([r["passed"] for r in done["rounds"]], [False, True])
        self.assertEqual(len(self.producer.calls), 2)
        ids = [r["skill_id"] for r in done["rounds"]]
        self.assertEqual(len(set(ids)), 2)
        for identifier in ids:
            self.assertEqual(self.catalog.describe(identifier)["version_status"], "candidate")
        self.assertEqual(tree_hashes(self.catalog.locate(ids[0])), self.target_files)
        newer = self.catalog.locate(ids[1])
        self.assertIn("本轮独立候选", (newer / "SKILL.md").read_text(encoding="utf-8"))
        self.assertEqual((newer / "assets/reference.png").read_bytes(),
            (self.catalog.locate(self.fixture["skill_id"]) / "assets/reference.png").read_bytes())
        for request in self.producer.calls:
            references = request["task_references"]
            self.assertTrue(references.reference_presentation)
            self.assertEqual(references.source_user_text, "保留优秀参考中的具体动作，不能覆盖共享Skill。")
            self.assertEqual(len(references.images), len(created["references"]))
            self.assertEqual(references.images[0].sha256, created["references"][0]["sha256"])
            self.assertTrue(Path(references.images[0].path).is_file())
        self.assertEqual(done["usage"]["calls"], len(self.evaluator.calls))
        self.assertEqual(done["usage"]["input_tokens"], len(self.evaluator.calls) * 100)
        self.assert_originals()

    def test_appended_judge_reassesses_baseline_and_candidate_same_version_preserving_old_scores(self):
        self.evaluator.append_on_round = 2
        done = self.drive(self.create()["id"])
        self.assertEqual(done["status"], "completed", done)
        self.assertEqual(done["judge"]["version"], 2)
        self.assertEqual(len(done["judge"]["criteria"]), 4)
        self.assertEqual(len(done["judge"]["history"]), 2)
        self.assertEqual(done["judge"]["threshold"], 80)
        self.assertEqual(done["judge"]["critical_floor"], 60)
        for round_ in done["rounds"]:
            self.assertEqual(round_["judge_version"], 2)
            self.assertEqual([e["judge_version"] for e in round_["evaluations"]], [1, 2])
            self.assertEqual(len(round_["scores"]), 4)
            self.assertEqual(len(round_["evaluations"][0]["scores"]), 3)
        judge_calls = [call for call in self.evaluator.calls if call["kind"] == "judge"]
        self.assertEqual([len(call["payload"]["criteria"]) for call in judge_calls], [3, 3, 4, 4])
        self.assertEqual([call["payload"]["allow_additions"] for call in judge_calls], [True, True, False, False])
        self.assertEqual(len(self.producer.calls), 2)
        self.assert_originals()

    def test_three_round_limit_keeps_best_and_explicit_continue_is_idempotent(self):
        self.evaluator.scores = (70, 60, 50)
        created = self.create()
        exhausted = self.drive(created["id"])
        self.assertEqual(exhausted["status"], "exhausted", exhausted)
        self.assertEqual(exhausted["best_round"], 1)
        self.assertEqual(exhausted["best_score"], 70)
        self.assertEqual(len(self.producer.calls), 3)
        for _ in range(5):
            self.iterations.step(created["id"])
        self.assertEqual(len(self.producer.calls), 3)
        continued = self.iterations.action(created["id"], action="continue",
            expected_revision=exhausted["revision"], request_id="continue-once")
        self.assertEqual(continued["max_rounds"], 6)
        self.assertEqual(continued["rounds"], exhausted["rounds"])
        self.assertEqual(continued["status"], "queued")
        again = self.iterations.action(created["id"], action="continue",
            expected_revision=exhausted["revision"], request_id="continue-once")
        self.assertEqual(again["revision"], continued["revision"])
        self.assertEqual(again["max_rounds"], 6)
        self.assertEqual(len(self.producer.calls), 3)
        self.assert_originals()

    def test_codex_failure_is_terminal_until_authorized_and_does_not_resubmit(self):
        requests = []
        class Failure:
            def produce_to(self, **request):
                requests.append(request)
                raise RuntimeError("controlled Codex transport failure; no generated image")
        self.extractions.producer_factory = Failure
        created = self.create()
        failed = self.drive(created["id"])
        self.assertEqual(failed["status"], "failed", failed)
        self.assertEqual(len(requests), 1)
        self.assertEqual(failed["rounds"][0]["status"], "failed")
        job = self.extractions.get(failed["rounds"][0]["trial_job_id"])
        self.assertEqual(len(job["trials"]), 1)
        old_trial = deepcopy(job["trials"][0])
        for _ in range(5):
            self.iterations.step(created["id"])
        self.assertEqual(self.create()["id"], created["id"])
        self.assertEqual(len(requests), 1)
        self.assertEqual(self.extractions.get(job["id"])["trials"][0], old_trial)
        self.assert_originals()

    def test_pause_while_codex_runs_preserves_delivery_and_resume_does_not_repeat(self):
        entered, release = Event(), Event()
        requests = []
        parent = ControlledCalibrationProducer()
        class Blocking:
            def produce_to(self, **request):
                requests.append(request)
                entered.set()
                if not release.wait(10):
                    raise RuntimeError("test did not release its controlled producer")
                return parent.produce_to(**request)
        self.extractions.producer_factory = Blocking
        self.addCleanup(release.set)
        created = self.create()
        self.iterations.step(created["id"])  # rubric
        self.iterations.step(created["id"])  # independent baseline
        self.iterations.step(created["id"])  # durable trial authorization
        self.assertTrue(entered.wait(5))
        current = self.iterations.get(created["id"])
        paused = self.iterations.action(created["id"], action="pause", expected_revision=current["revision"], request_id="pause-codex")
        release.set()
        self.extractions.worker.join(10)
        calls = len(self.evaluator.calls)
        self.iterations.step(created["id"])
        self.assertEqual(self.iterations.get(created["id"])["status"], "paused")
        self.assertEqual(len(requests), 1)
        self.assertEqual(len(self.evaluator.calls), calls)
        self.evaluator.scores = (90,)
        resumed = self.iterations.action(created["id"], action="resume", expected_revision=paused["revision"], request_id="resume-once")
        done = self.drive(created["id"])
        self.assertEqual(done["status"], "completed", done)
        self.assertEqual(len(requests), 1)
        self.assertGreater(done["revision"], resumed["revision"])
        self.assert_originals()

    def test_actions_use_compare_and_swap_and_request_replay_not_repeated_state_changes(self):
        created = self.create()
        paused = self.iterations.action(created["id"], action="pause", expected_revision=created["revision"], request_id="pause-one")
        replay = self.iterations.action(created["id"], action="pause", expected_revision=created["revision"], request_id="pause-one")
        self.assertEqual(replay["revision"], paused["revision"])
        for values in (dict(action="resume", expected_revision=created["revision"], request_id="stale-resume"),
                       dict(action="resume", expected_revision=paused["revision"], request_id="pause-one")):
            with self.assertRaises(ExtractionError):
                self.iterations.action(created["id"], **values)
        resumed = self.iterations.action(created["id"], action="resume", expected_revision=paused["revision"], request_id="resume-one")
        self.assertEqual(resumed["status"], "queued")
        self.assertEqual(len(self.producer.calls), 0)
        with self.assertRaises(ExtractionError):
            self.create(instructions="different request with same id")
        self.assertEqual(self.create()["id"], created["id"])
        self.assert_originals()

    def test_unknown_model_request_on_restart_pauses_without_auto_retry_or_production(self):
        created = self.create()
        with self.db.session() as db:
            row = db.get(SkillIteration, created["id"])
            state = deepcopy(row.state_json)
            state["calls"]["rubric"] = dict(status="inflight", kind="rubric", attempt=1, owner="old-process")
            state["usage"]["calls"] = 1
            row.state_json, row.status = state, "running"
        self.iterations.start()
        self.iterations.shutdown()
        view = self.iterations.get(created["id"])
        self.assertEqual(view["status"], "interrupted")
        self.assertIn("未知", view["error"])
        with self.db.session() as db:
            self.assertEqual(db.get(SkillIteration, created["id"]).state_json["calls"]["rubric"]["status"], "unknown")
        self.assertEqual(self.evaluator.calls, [])
        self.assertEqual(self.producer.calls, [])
        self.iterations.stopping.clear()
        self.iterations.step(created["id"])
        self.assertEqual(self.evaluator.calls, [])
        self.assert_originals()

    def test_durable_trial_with_lost_submit_response_is_reconciled_before_any_new_round(self):
        self.evaluator.scores = (90,)
        created = self.create()
        identifier = created["id"]
        self.iterations.step(identifier)
        self.iterations.step(identifier)
        trial = self.app.state.skill_versions.trial
        receipts = []

        def lost(*args, **kwargs):
            result = trial(*args, **kwargs)
            receipts.append(result)
            raise TimeoutError("controlled: trial accepted but response lost before RSI state commit")

        with patch.object(self.app.state.skill_versions, "trial", lost):
            self.iterations.step(identifier)
        self.extractions.worker.join(10)
        failed = self.iterations.get(identifier)
        self.assertEqual(failed["status"], "failed", failed)
        self.assertEqual(len(receipts), 1)
        self.assertNotIn("trial_job_id", failed["rounds"][0])
        self.assertEqual(len(self.producer.calls), 1)
        job_id = receipts[0]["id"]
        original_job = tree_hashes(self.extractions._path("jobs", job_id))
        self.iterations.action(identifier, action="continue", expected_revision=failed["revision"], request_id="reconcile-submit")
        done = self.drive(identifier)
        self.assertEqual(done["status"], "completed", done)
        self.assertEqual(len(done["rounds"]), 1)
        self.assertEqual(done["rounds"][0]["trial_job_id"], job_id)
        self.assertEqual(len(self.producer.calls), 1)
        self.assertEqual(tree_hashes(self.extractions._path("jobs", job_id)), original_job)
        self.assert_originals()

    def test_disk_model_response_after_lost_commit_is_adopted_only_after_resume_without_second_call(self):
        created = self.create()
        update = self.iterations._update

        def lose_finish(identifier, change):
            if change.__name__ == "finish":
                # The model already returned and response.json was written.
                # Shutdown marks this request unknown before its DB receipt.
                self.iterations.shutdown()
                raise RuntimeError("controlled lost DB completion after response persisted")
            return update(identifier, change)

        with patch.object(self.iterations, "_update", lose_finish):
            self.iterations.step(created["id"])
        interrupted = self.iterations.get(created["id"])
        self.assertEqual(interrupted["status"], "interrupted", interrupted)
        self.assertEqual(len(self.evaluator.calls), 1)
        response = self.iterations._path(created["id"]) / "model/rubric-1/response.json"
        original = response.read_bytes()
        self.assertEqual(self.producer.calls, [])
        self.iterations.stopping.clear()
        self.iterations.step(created["id"])
        self.assertEqual(len(self.evaluator.calls), 1)
        self.iterations.action(created["id"], action="resume", expected_revision=interrupted["revision"], request_id="adopt-model-reply")
        self.iterations.step(created["id"])
        recovered = self.iterations.get(created["id"])
        self.assertEqual(recovered["stage"], "prepare", recovered)
        self.assertEqual(recovered["judge"]["version"], 1)
        self.assertEqual(len(self.evaluator.calls), 1)
        self.assertEqual(recovered["usage"]["calls"], 1)
        self.assertEqual(recovered["usage"]["input_tokens"], 100)
        self.assertEqual(response.read_bytes(), original)
        self.assertTrue(any(event["kind"] == "model_recovered" for event in recovered["events"]))
        self.assert_originals()

    def test_account_listing_hides_transferred_series_from_both_account_histories(self):
        created = self.create()
        creator_id = self.fixture["creator_id"]
        self.assertEqual([item["id"] for item in self.iterations.list(creator_id=creator_id)["items"]], [created["id"]])
        ContentRepository(self.db).create_creator(creator_id="transferred-account", display_name="Other account")
        with self.db.session() as db:
            db.get(Series, self.fixture["series_id"]).creator_id = "transferred-account"
        self.assertEqual(self.iterations.list(creator_id=creator_id)["items"], [])
        self.assertEqual(self.iterations.list(creator_id="transferred-account")["items"], [])
        self.iterations.step(created["id"])
        self.assertEqual(self.iterations.get(created["id"])["status"], "failed")
        self.assertEqual(self.evaluator.calls, [])
        self.assertEqual(self.producer.calls, [])

    def test_continue_adds_at_most_three_and_never_exceeds_thirty(self):
        created = self.create()
        with self.db.session() as db:
            row = db.get(SkillIteration, created["id"])
            state = deepcopy(row.state_json)
            state.update(max_rounds=29, phase="finished")
            row.state_json, row.status = state, "exhausted"
        continued = self.iterations.action(created["id"], action="continue",
            expected_revision=created["revision"], request_id="last-one")
        self.assertEqual(continued["max_rounds"], 30)
        with self.db.session() as db:
            row = db.get(SkillIteration, created["id"])
            state = deepcopy(row.state_json)
            state["phase"] = "finished"
            row.state_json, row.status, row.revision = state, "exhausted", row.revision + 1
        current = self.iterations.get(created["id"])
        with self.assertRaises(ExtractionError):
            self.iterations.action(created["id"], action="continue", expected_revision=current["revision"], request_id="over-cap")
        self.assertEqual(self.iterations.get(created["id"])["max_rounds"], 30)
        self.assertEqual(self.evaluator.calls, [])
        self.assertEqual(self.producer.calls, [])
        self.assert_originals()

    def test_rubric_uses_frozen_role_and_references_when_source_body_is_unavailable(self):
        created = self.create()
        source = self.catalog.locate(self.fixture["skill_id"])
        moved = source / "temporarily-unavailable.md"
        (source / "SKILL.md").rename(moved)
        try:
            self.iterations.step(created["id"])
            current = self.iterations.get(created["id"])
            self.assertEqual(current["stage"], "prepare", current)
            self.assertEqual(current["judge"]["version"], 1)
            self.assertEqual(self.evaluator.calls[0]["payload"]["role"], "mind")
            self.assertEqual(self.evaluator.calls[0]["images"][0][2], created["references"][0]["sha256"])
            self.assertIn("references", self.evaluator.calls[0]["images"][0][1])
            self.assertEqual(self.producer.calls, [])
        finally:
            moved.rename(source / "SKILL.md")
        self.assert_originals()

    def test_candidate_edit_after_scoring_cannot_be_claimed_as_passed_delivery(self):
        self.evaluator.scores = (90,)
        created = self.create()
        rated = self.drive(created["id"], until=lambda current: bool(current["rounds"])
            and current["rounds"][0].get("judge_version") == 1)
        self.assertEqual(rated["status"], "queued", rated)
        self.assertTrue(rated["rounds"][0]["passed"])
        candidate = rated["rounds"][0]["skill_id"]
        file = self.catalog.read_skill_file(candidate, "SKILL.md")
        self.catalog.update_skill_file(candidate, "SKILL.md", file["content"] + "\nUser edited after the evaluated trial.\n", file["digest"])
        self.iterations.step(created["id"])
        rejected = self.iterations.get(created["id"])
        self.assertEqual(rejected["status"], "failed", rejected)
        self.assertIn("评分", rejected["error"])
        self.assertIsNone(rejected["best_skill_id"])
        self.assertEqual(len(self.producer.calls), 1)
        self.assert_originals()

    def test_paused_finished_stage_resumes_to_delivery_without_new_model_or_production(self):
        self.evaluator.scores = (90,)
        done = self.drive(self.create()["id"])
        self.assertEqual(done["status"], "completed", done)
        with self.db.session() as db:
            row = db.get(SkillIteration, done["id"])
            self.assertEqual(row.state_json["phase"], "finished")
            # Deterministically represent pause winning the final status race.
            row.status, row.revision = "paused", row.revision + 1
        paused = self.iterations.get(done["id"])
        model_calls, productions = len(self.evaluator.calls), len(self.producer.calls)
        self.iterations.action(done["id"], action="resume", expected_revision=paused["revision"], request_id="resume-finished")
        resumed = self.drive(done["id"])
        self.assertEqual(resumed["status"], "completed", resumed)
        self.assertEqual(resumed["best_skill_id"], done["best_skill_id"])
        self.assertEqual(resumed["rounds"], done["rounds"])
        self.assertEqual(len(self.evaluator.calls), model_calls)
        self.assertEqual(len(self.producer.calls), productions)
        self.assert_originals()

    def test_pause_resume_during_same_owner_model_call_accepts_reply_without_retry_or_false_failure(self):
        entered, release = Event(), Event()
        evaluate = self.evaluator

        def blocked(kind, payload, images, provider_factory):
            entered.set()
            if not release.wait(10):
                raise RuntimeError("test did not release its controlled DS request")
            return evaluate(kind, payload, images, provider_factory)

        self.iterations.evaluator = blocked
        created = self.create()
        runner = Thread(target=self.iterations.step, args=(created["id"],))
        self.addCleanup(release.set)
        runner.start()
        try:
            self.assertTrue(entered.wait(5))
            current = self.iterations.get(created["id"])
            paused = self.iterations.action(created["id"], action="pause", expected_revision=current["revision"], request_id="pause-ds")
            self.iterations.action(created["id"], action="resume", expected_revision=paused["revision"], request_id="resume-ds-inflight")
            with self.db.session() as db:
                call = db.get(SkillIteration, created["id"]).state_json["calls"]["rubric"]
                self.assertEqual(call["status"], "inflight")
                self.assertEqual(call["owner"], self.iterations.owner)
        finally:
            release.set()
            runner.join(10)
        self.assertFalse(runner.is_alive())
        resumed = self.iterations.get(created["id"])
        self.assertEqual(resumed["status"], "queued", resumed)
        self.assertEqual(resumed["stage"], "prepare")
        self.assertEqual(len(self.evaluator.calls), 1)
        self.assertEqual(resumed["usage"]["calls"], 1)
        self.assertEqual(self.producer.calls, [])
        self.assert_originals()

    def test_http_create_detail_images_and_action_replay_have_no_automatic_activation(self):
        payload = dict(series_id=self.fixture["series_id"], skill_id=self.fixture["skill_id"],
            topic_id=self.runs.get(self.fixture["run_id"]).topic_id, request_id=uuid4().hex,
            instructions="HTTP受控闭环。", max_rounds=1)
        first = self.client.post("/api/skill-iterations", json=payload)
        self.assertEqual(first.status_code, 202, first.text)
        replay = self.client.post("/api/skill-iterations", json=payload)
        self.assertEqual(replay.json()["id"], first.json()["id"])
        identifier = first.json()["id"]
        reference = first.json()["references"][0]
        image = self.client.get(reference["url"])
        self.assertEqual(image.status_code, 200)
        self.assertEqual(hashlib.sha256(image.content).hexdigest(), reference["sha256"])
        done = self.drive(identifier)
        self.assertEqual(done["status"], "exhausted", done)
        details = self.client.get("/api/skill-iterations/" + identifier)
        self.assertEqual(details.json()["rounds"], done["rounds"])
        output = self.client.get(done["rounds"][0]["images"][0]["url"])
        self.assertEqual(output.status_code, 200)
        self.assertEqual(self.client.get("/api/skill-iterations/" + identifier + "/files/private").status_code, 404)
        self.assert_originals()


if __name__ == "__main__":
    unittest.main()
