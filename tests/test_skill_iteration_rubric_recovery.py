"""Rubric recovery with isolated SQLite and controlled responses, no live models."""
from copy import deepcopy
import json
from threading import Event, Thread
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from creatoros.integrations.skill_iteration_judge import ReplyEmptyError, ReplyLengthError, evaluate, parse_reply
from creatoros.integrations.skill_iterations import SkillIterationService
from creatoros.ai.types import ModelUsage
from tests.test_skill_iteration_judge_recovery import ScriptedProvider
from tests import test_skill_iteration_service as fixtures


def valid_rubric():
    return {"criteria": [
        {"description": name, "evidence": "参考图一中可检查的内容", "critical": False, "quality_kind": "content"}
        for name in ("故事要有具体行动", "人物动机清晰", "结尾呼应前文")], "workflow_constraints": []}


class RubricRecoveryTests(unittest.TestCase):
    setUp = fixtures.SkillIterationServiceTests.setUp
    create = fixtures.SkillIterationServiceTests.create
    assert_originals = fixtures.SkillIterationServiceTests.assert_originals

    def use_replies(self, *replies):
        provider = ScriptedProvider(replies)
        self.iterations.evaluator = evaluate
        self.iterations.provider_factory = provider
        return provider

    def restart_without_worker(self, provider):
        service = SkillIterationService(self.db, self.app.state.skill_versions, self.app.state.chat,
                                        evaluator=evaluate, provider_factory=provider)
        with patch("creatoros.integrations.skill_iterations.Thread"):
            service.start()
        self.iterations = service
        return service

    def test_empty_rubric_then_valid_reply_repairs_once_before_any_trial(self):
        item = self.create(max_rounds=1)
        provider = self.use_replies("", valid_rubric())
        self.iterations.step(item["id"])
        result = self.iterations.get(item["id"])
        self.assertEqual(result["stage"], "prepare", result)
        self.assertEqual(result["round_count"], 0)
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(self.producer.calls, [])
        self.assertEqual(result["usage"], {"input_tokens": 206, "output_tokens": 38, "calls": 2})
        call = result["model_calls"]["rubric"]
        self.assertEqual([row["status"] for row in call["attempts"]], ["invalid_response", "completed"])
        self.assertTrue(all(row["usage_accounted"] for row in call["attempts"]))
        base = self.iterations._path(item["id"]) / "model"
        first = json.loads((base / "rubric-1/raw-response.json").read_text(encoding="utf-8"))
        second = json.loads((base / "rubric-2/raw-response.json").read_text(encoding="utf-8"))
        self.assertEqual(first["content"], "")
        self.assertEqual(json.loads(second["content"]), valid_rubric())
        self.assertEqual((base / "rubric-1/request.json").read_bytes(), (base / "rubric-2/request.json").read_bytes())
        self.assertTrue((base / "rubric-2/repair.json").is_file())
        self.assert_originals()

    def test_two_invalid_responses_stop_with_original_receipts_and_manual_resume(self):
        item = self.create(max_rounds=1)
        provider = self.use_replies("", "not json")
        self.iterations.step(item["id"])
        result = self.iterations.get(item["id"])
        self.assertEqual((result["status"], result["stage"]), ("failed", "rubric"))
        self.assertEqual(result["allowed_actions"], ["resume"])
        self.assertEqual(result["next_action"], "resume_current_model_stage")
        self.assertEqual(result["usage"], {"input_tokens": 206, "output_tokens": 38, "calls": 2})
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(self.producer.calls, [])
        base = self.iterations._path(item["id"]) / "model"
        self.assertEqual([json.loads((base / f"rubric-{n}/raw-response.json").read_text(encoding="utf-8"))["content"]
                          for n in (1, 2)], ["", "not json"])
        for _ in range(3):
            self.iterations.step(item["id"])
        self.assertEqual(len(provider.calls), 2)
        prior = [(base / f"rubric-{n}/raw-response.json").read_bytes() for n in (1, 2)]
        resumed_provider = self.use_replies(valid_rubric())
        current = self.iterations.get(item["id"])
        self.iterations.action(item["id"], action="resume", expected_revision=current["revision"], request_id="resume-invalid")
        self.iterations.step(item["id"])
        resumed = self.iterations.get(item["id"])
        self.assertEqual(resumed["stage"], "prepare")
        self.assertEqual(resumed["usage"], {"input_tokens": 309, "output_tokens": 57, "calls": 3})
        self.assertEqual(len(resumed_provider.calls), 1)
        self.assertEqual([(base / f"rubric-{n}/raw-response.json").read_bytes() for n in (1, 2)], prior)
        self.assertEqual(self.producer.calls, [])
        self.assert_originals()

    def test_unknown_second_response_never_retries_automatically(self):
        item = self.create(max_rounds=1)
        provider = self.use_replies("", TimeoutError("controlled unknown response"), valid_rubric())
        self.iterations.step(item["id"])
        result = self.iterations.get(item["id"])
        self.assertEqual((result["status"], result["stage"]), ("failed", "rubric"))
        self.assertEqual(result["model_calls"]["rubric"]["status"], "unknown")
        self.assertEqual(len(provider.calls), 2)
        for _ in range(3):
            self.iterations.step(item["id"])
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(self.producer.calls, [])
        self.assert_originals()

    def test_unknown_first_response_requires_explicit_resume_before_retry(self):
        item = self.create(max_rounds=1)
        provider = self.use_replies(TimeoutError("controlled unknown response"), valid_rubric())
        self.iterations.step(item["id"])
        result = self.iterations.get(item["id"])
        self.assertEqual(result["model_calls"]["rubric"]["status"], "unknown")
        restarted = self.restart_without_worker(provider)
        for _ in range(3):
            restarted.step(item["id"])
        self.assertEqual(len(provider.calls), 1)
        current = restarted.get(item["id"])
        restarted.action(item["id"], action="resume", expected_revision=current["revision"], request_id="resume-unknown")
        restarted.step(item["id"])
        self.assertEqual(restarted.get(item["id"])["stage"], "prepare")
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(self.producer.calls, [])

    def test_empty_response_without_finish_reason_does_not_claim_length_limit(self):
        with self.assertRaises(ReplyEmptyError) as error:
            parse_reply("rubric", {"content": "", "tool_calls": []})
        self.assertIn("原因暂不能确定", str(error.exception))
        self.assertNotIn("长度", str(error.exception))

    def test_explicit_length_reason_cannot_be_accepted_even_if_partial_json_parses(self):
        with self.assertRaises(ReplyLengthError):
            parse_reply("rubric", {"content": json.dumps(valid_rubric()), "finish_reason": "length"})

    def test_shutdown_during_request_keeps_response_without_automatic_retry(self):
        entered, release = Event(), Event()
        self.addCleanup(release.set)
        def blocking_empty():
            entered.set()
            if not release.wait(10):
                raise TimeoutError("controlled wait not released")
            return ""
        item = self.create(max_rounds=1)
        provider = self.use_replies(blocking_empty, valid_rubric())
        worker = Thread(target=self.iterations.step, args=(item["id"],))
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            self.iterations.shutdown()
        finally:
            release.set()
            worker.join(10)
        self.assertFalse(worker.is_alive())
        result = self.iterations.get(item["id"])
        self.assertEqual(result["status"], "interrupted", result)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(self.producer.calls, [])
        self.assertTrue((self.iterations._path(item["id"]) / "model/rubric-1/raw-response.json").is_file())
        restarted = self.restart_without_worker(provider)
        before = restarted.get(item["id"])
        restarted.action(item["id"], action="resume", expected_revision=before["revision"], request_id="manual-resume")
        restarted.step(item["id"])
        self.assertEqual(restarted.get(item["id"])["stage"], "prepare")
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(self.producer.calls, [])
        self.assert_originals()

    def test_restart_after_persisted_retry_authorization_does_not_repeat_first_call(self):
        item = self.create(max_rounds=1)
        provider = self.use_replies("", valid_rubric())
        original = self.iterations._model
        def simulate_power_loss(row, kind, key, payload, images):
            if provider.calls:
                raise SystemExit("controlled loss after retry was recorded")
            return original(row, kind, key, payload, images)
        with patch.object(self.iterations, "_model", side_effect=simulate_power_loss):
            with self.assertRaises(SystemExit):
                self.iterations.step(item["id"])
        state = self.iterations.get(item["id"])
        self.assertEqual(state["model_calls"]["rubric"]["status"], "retry_authorized")
        self.assertEqual(len(provider.calls), 1)
        saved = (self.iterations._path(item["id"]) / "model/rubric-1/raw-response.json").read_bytes()
        restarted = self.restart_without_worker(provider)
        restarted.step(item["id"])
        result = restarted.get(item["id"])
        self.assertEqual(result["stage"], "prepare", result)
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(self.producer.calls, [])
        self.assertEqual((restarted._path(item["id"]) / "model/rubric-1/raw-response.json").read_bytes(), saved)
        self.assert_originals()

    def test_restart_recovers_saved_invalid_reply_and_missing_usage_before_repair(self):
        item = self.create(max_rounds=1)
        provider = self.use_replies("", valid_rubric())
        with patch.object(self.iterations, "_record_model_receipt", side_effect=RuntimeError("controlled receipt write failure")):
            self.iterations.step(item["id"])
        before = self.iterations.get(item["id"])
        self.assertEqual((before["status"], before["stage"]), ("failed", "rubric"))
        self.assertEqual(before["usage"]["calls"], 1)
        self.assertEqual(before["usage"]["input_tokens"], 0)
        old_path = self.iterations._path(item["id"]) / "model/rubric-1/raw-response.json"
        saved = old_path.read_bytes()
        restarted = self.restart_without_worker(provider)
        restarted.action(item["id"], action="resume", expected_revision=before["revision"], request_id="manual-recover-raw")
        restarted.step(item["id"])
        result = restarted.get(item["id"])
        self.assertEqual(result["stage"], "prepare", result)
        self.assertEqual(result["usage"], {"input_tokens": 206, "output_tokens": 38, "calls": 2})
        self.assertEqual(old_path.read_bytes(), saved)
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(self.producer.calls, [])
        self.assert_originals()

    def test_output_length_failures_keep_both_finish_reasons_and_actionable_message(self):
        item = self.create(max_rounds=1)
        calls = []
        class Provider:
            def complete(self, context):
                calls.append(context.max_output_tokens)
                return SimpleNamespace(content="", tool_calls=[], usage=ModelUsage(103, 8192, 8295),
                                       finish_reason="length", reasoning_content="do not persist")
        self.iterations.evaluator, self.iterations.provider_factory = evaluate, Provider
        self.iterations.step(item["id"])
        result = self.iterations.get(item["id"])
        self.assertEqual((result["status"], result["stage"]), ("failed", "rubric"))
        self.assertIn("长度", result["error"])
        self.assertEqual(calls, [8192, 8192])
        self.assertEqual(result["usage"], {"input_tokens": 206, "output_tokens": 16384, "calls": 2})
        base = self.iterations._path(item["id"]) / "model"
        for n in (1, 2):
            raw = json.loads((base / f"rubric-{n}/raw-response.json").read_text(encoding="utf-8"))
            self.assertEqual(raw["finish_reason"], "length")
            self.assertEqual(raw["max_output_tokens"], 8192)
            self.assertNotIn("reasoning_content", raw)
        self.assertEqual(self.producer.calls, [])

    def test_content_filter_is_not_an_automatic_format_retry(self):
        item = self.create(max_rounds=1)
        calls = []
        class Provider:
            def complete(self, context):
                calls.append(1)
                return SimpleNamespace(content="", tool_calls=[], usage=ModelUsage(103, 19, 122),
                                       finish_reason="content_filter", reasoning_content=None)
        self.iterations.evaluator, self.iterations.provider_factory = evaluate, Provider
        self.iterations.step(item["id"])
        result = self.iterations.get(item["id"])
        self.assertEqual((result["status"], result["stage"]), ("failed", "rubric"))
        self.assertEqual(len(calls), 1)
        self.assertIn("过滤", result["error"])
        self.assertEqual(self.producer.calls, [])
        provider = self.use_replies(valid_rubric())
        self.iterations.action(item["id"], action="resume", expected_revision=result["revision"], request_id="manual-retry-filter")
        self.iterations.step(item["id"])
        self.assertEqual(self.iterations.get(item["id"])["stage"], "prepare")
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(self.producer.calls, [])
