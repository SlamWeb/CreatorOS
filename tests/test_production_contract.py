"""Current model production contract and its pure DTO-to-HTTP translation.

These local mapping tests do not start a producer or stand in for API acceptance.
"""
from copy import deepcopy
from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator
from pydantic import ValidationError

from creatoros.ai.types import ToolCall
from creatoros.runs.production_tasks import ProductionTaskInput
from creatoros.tools import execute_tool_call
from creatoros.tools.production_arguments import StartRunArgs
from creatoros.tools.studio import start_content_run
from creatoros.web.reference_routes import ReferenceProductionRequest
from creatoros.web.schemas import RunBatchStartRequest, RunStartRequest


SKILLS = ["layout--" + "1" * 16, "copy--" + "2" * 16, "extra--" + "3" * 16]
IMAGE = {"kind": "image", "title": "开场", "brief": "独立制作这一张。"}
VIDEO = {"kind": "video", "title": "剪辑", "brief": "按配乐制作。",
         "inputs": {"collection_id": "collection", "bgm_reference": "用户给的分享原文 https://example.com/bgm"}}


class ProductionContractTests(unittest.TestCase):
    def setUp(self):
        self.schema = StartRunArgs.model_json_schema()
        self.validator = Draft202012Validator(self.schema)

    def assert_valid(self, payload):
        self.validator.validate(payload)
        return StartRunArgs.model_validate(payload)

    def assert_invalid(self, payload):
        self.assertTrue(list(self.validator.iter_errors(payload)), payload)
        with self.assertRaises(ValidationError):
            StartRunArgs.model_validate(payload)

    def test_current_actions_match_schema_and_host(self):
        for payload in (
            {"action": "start", "topic_ids": ["topic"]},
            {"action": "tasks", "topic_id": "topic", "tasks": [IMAGE, VIDEO]},
            {"action": "fresh", "source_run_id": "run", "expected_version": 2},
            {"action": "reference", "series_id": "series", "title": "参考制作", "attachment_ids": ["image"]},
        ):
            with self.subTest(payload=payload):
                self.assert_valid(payload)

    def test_single_two_and_multiple_skill_selections_need_no_roles(self):
        for selected in (SKILLS[:1], SKILLS[:2], SKILLS):
            for payload in (
                {"action": "tasks", "topic_id": "topic", "tasks": [{**IMAGE, "skill_ids": selected}]},
                {"action": "tasks", "topic_id": "topic", "tasks": [{**VIDEO, "skill_ids": selected}]},
                {"action": "fresh", "source_run_id": "run", "expected_version": 2, "skill_ids": selected},
            ):
                with self.subTest(payload=payload):
                    self.assert_valid(payload)

    def test_legacy_and_mixed_action_fields_are_rejected_in_both_contracts(self):
        for payload in (
            {"topic_id": "topic"},
            {"topic_ids": ["topic"]},
            {"topic_id": "topic", "tasks": [IMAGE]},
            {"action": "start", "topic_id": "topic"},
            {"action": "start", "topic_ids": ["topic"], "mode": "start"},
            {"action": "start", "topic_ids": ["topic"], "skill_ids": SKILLS},
            {"action": "fresh", "source_run_id": "run", "expected_version": 2, "topic_id": "topic"},
            {"action": "fresh", "source_run_id": "run", "expected_version": 2, "skill_overrides": {}},
            {"action": "tasks", "topic_id": "topic", "tasks": [IMAGE], "series_id": "series"},
            {"action": "reference", "series_id": "series", "title": "x", "attachment_ids": []},
        ):
            with self.subTest(payload=payload):
                self.assert_invalid(payload)

    def test_task_kind_and_video_only_fields_are_schema_visible(self):
        for task in (
            {"title": "x", "brief": "x"},
            {**IMAGE, "skills": [{"skill_id": SKILLS[0], "role": "primary"}]},
            {**IMAGE, "skill_overrides": {}},
            {**IMAGE, "inputs": VIDEO["inputs"]},
            {**IMAGE, "depends_on": []},
            {"kind": "video", "title": "x", "brief": "x"},
        ):
            with self.subTest(task=task):
                self.assert_invalid({"action": "tasks", "topic_id": "topic", "tasks": [task]})

    def test_bgm_sources_are_exclusive_without_a_resolution_step(self):
        common = {"collection_id": "collection"}
        for inputs in (
            {**common, "bgm_material_id": "a" * 64},
            {**common, "bgm_reference": "分享原文，无需预解析"},
        ):
            self.assert_valid({"action": "tasks", "topic_id": "topic", "tasks": [{**VIDEO, "inputs": inputs}]})
        for inputs in (common, {**common, "bgm_material_id": "a" * 64, "bgm_reference": "原文"}):
            self.assert_invalid({"action": "tasks", "topic_id": "topic", "tasks": [{**VIDEO, "inputs": inputs}]})


class RequestMapping:
    """Record pure transport mapping; response bodies are local DTO examples."""
    def __init__(self):
        self.calls = []
        self.base_url = "http://127.0.0.1:8765"
        self.run = {"id": "new-run", "topic_id": "topic", "status": "queued", "version": 1,
                    "active_revision_number": 1, "revisions": [{"attempts": []}]}

    def start_batch(self, topic_ids):
        payload = {"topic_ids": topic_ids}
        RunBatchStartRequest.model_validate(payload)
        self.calls.append(("POST", "/api/runs/batch", deepcopy(payload)))
        return {"items": [{"topic_id": topic_ids[0], "run_id": "run", "accepted": True}], "accepted_count": 1}

    def get_run(self, run_id):
        self.calls.append(("GET", "/api/runs/" + run_id, None))
        return {"id": run_id, "topic_id": "topic"}

    def request(self, method, path, *, payload):
        self.calls.append((method, path, deepcopy(payload)))
        if path.endswith("/production-tasks"):
            for task in payload["tasks"]:
                ProductionTaskInput.model_validate(task)
            return {"topic_id": "topic", "creator_id": "creator", "collection_id": "collection",
                    "items": [{"id": "task", "run_id": "new-run", "status": "queued"}]}
        if path.endswith("/image-actions/reference"):
            return {"id": "a" * 64}
        if path.endswith("/reference-production"):
            ReferenceProductionRequest.model_validate(payload)
            return {**self.run, "production_queue": {"status": "pending"}}
        if path == "/api/runs":
            RunStartRequest.model_validate(payload)
            return deepcopy(self.run)
        if path.endswith("/execute"):
            return deepcopy(self.run)
        raise AssertionError(path)

    def run_summary(self, run, *, accepted=False):
        return {"run_id": run["id"], "topic_id": run["topic_id"], "accepted": accepted}


class ProductionRequestMappingTests(unittest.TestCase):
    def setUp(self):
        self.mapping = RequestMapping()
        self.context = SimpleNamespace(user_request_id="turn", agent_session_id="session", creator_id="creator")
        self.adapter = patch("creatoros.tools.studio._call", lambda action, context=None: action(self.mapping))
        self.adapter.start()
        self.addCleanup(self.adapter.stop)

    def test_start_always_uses_batch_request_even_for_one_topic(self):
        result = start_content_run(action="start", topic_ids=["topic"], context=self.context)
        self.assertEqual(self.mapping.calls, [("POST", "/api/runs/batch", {"topic_ids": ["topic"]})])
        self.assertEqual(result["items"][0]["run_id"], "run")

    def test_tasks_map_exact_ids_and_shared_instructions_to_business_dto(self):
        task = {**IMAGE, "skill_ids": SKILLS}
        result = start_content_run(action="tasks", topic_id="topic", tasks=[task],
                                   instructions="保持人物一致", context=self.context)
        method, path, payload = self.mapping.calls[0]
        self.assertEqual((method, path), ("POST", "/api/topics/topic/production-tasks"))
        self.assertEqual(payload["tasks"][0]["skill_ids"], SKILLS)
        self.assertEqual(payload["tasks"][0]["brief"], "共同要求：\n保持人物一致\n\n本任务：\n独立制作这一张。")
        self.assertEqual(result["items"][0]["url"], "/runs/new-run")
        first = payload["request_id"]
        start_content_run(action="tasks", topic_id="topic", tasks=[task],
                          instructions="保持人物一致", context=self.context)
        self.assertEqual(self.mapping.calls[-1][2]["request_id"], first)

    def test_video_share_text_is_forwarded_without_import_or_preparse(self):
        start_content_run(action="tasks", topic_id="topic", tasks=[VIDEO], context=self.context)
        self.assertEqual(len(self.mapping.calls), 1)
        self.assertEqual(self.mapping.calls[0][2]["tasks"][0]["inputs"], VIDEO["inputs"])

    def test_combined_brief_overflow_is_safe_to_correct_before_dispatch(self):
        payload = {"action": "tasks", "topic_id": "topic", "instructions": "共同要求",
                   "tasks": [{**IMAGE, "brief": "x" * 10000}]}
        StartRunArgs.model_validate(payload)
        with patch("creatoros.tools.studio._call") as dispatch:
            result = execute_tool_call(ToolCall("overflow", "start_content_run", json.dumps(payload)), self.context)
        dispatch.assert_not_called()
        self.assertTrue(result.is_error)
        self.assertEqual(result.error_type, "invalid_arguments")
        self.assertTrue(result.retryable)
        self.assertTrue(result.details["pre_dispatch"])
        self.assertEqual(self.mapping.calls, [])

    def test_fresh_reads_topic_identity_and_passes_selected_ids(self):
        result = start_content_run(action="fresh", source_run_id="old", expected_version=4,
                                   skill_ids=SKILLS, context=self.context)
        self.assertEqual(self.mapping.calls[0], ("GET", "/api/runs/old", None))
        payload = self.mapping.calls[1][2]
        self.assertEqual({key: payload[key] for key in ("topic_id", "mode", "source_run_id", "expected_version", "skill_ids")},
                         {"topic_id": "topic", "mode": "fresh", "source_run_id": "old", "expected_version": 4, "skill_ids": SKILLS})
        self.assertEqual(self.mapping.calls[2], ("POST", "/api/runs/new-run/execute", {"expected_version": 1}))
        self.assertTrue(result["accepted"])

    def test_reference_uses_existing_reference_then_production_routes(self):
        result = start_content_run(action="reference", series_id="series", title="参考制作",
            attachment_ids=["image"], instructions="完整要求", reference_presentation=True, context=self.context)
        first, second = self.mapping.calls
        self.assertEqual(first[:2], ("POST", "/api/agent/sessions/session/image-actions/reference"))
        self.assertEqual(first[2]["attachment_ids"], ["image"])
        self.assertTrue(first[2]["reference_presentation"])
        self.assertEqual(second[:2], ("POST", "/api/series/series/reference-production"))
        self.assertEqual(second[2]["reference_id"], "a" * 64)
        self.assertTrue(result["accepted"])

    def test_mixed_legacy_tool_fields_never_reach_mapping(self):
        with self.assertRaises(ValidationError):
            start_content_run(action="start", topic_ids=["topic"], skill_overrides={}, context=self.context)
        self.assertEqual(self.mapping.calls, [])


if __name__ == "__main__":
    unittest.main()
