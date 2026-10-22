"""Bounded Topic schema and history compatibility; no model/network calls here.

These pure argument/projection checks supplement real isolated HTTP regressions
and frontend DeepSeek validation; they are not a model-quality or E2E score.
"""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from creatoros.ai.types import ToolCall
from creatoros.context import RuntimeContext
from creatoros.integrations.studio import StudioClient, StudioClientError
from creatoros.storage import ContentAttempt, ContentRevision, ContentRun, Topic, WriteReceipt
from creatoros.tools import execute_tool_call, tools
from creatoros.tools.definitions import tool_registry
from creatoros.tools.host_contract import model_tool_schemas
from creatoros.tools.model_projection import project_model_data
from creatoros.web.chat import ACCOUNT_TOOLS
from creatoros.web.app import create_app
from tests import test_topic_management_http as topic_http
from tests.agent_studio_support import serve


class TopicToolContractTests(unittest.TestCase):
    def test_one_model_topic_surface_keeps_legacy_internal_registry(self):
        context = RuntimeContext(Path.cwd(), creator_id="account-a", allowed_tools=ACCOUNT_TOOLS)
        exposed = {row["function"]["name"]: row["function"]
                   for row in model_tool_schemas(tools, context)}
        self.assertIn("manage_topics", exposed)
        for old_name, arguments in (
            ("list_series_topics", {"series_id": "series-a"}),
            ("queue_topics", {"series_id": "series-a", "topics": [{"title": "旧内部调用"}]}),
        ):
            self.assertNotIn(old_name, exposed)
            self.assertIn(old_name, tool_registry)
            self.assertFalse(tool_registry[old_name].expose_to_model)
            self.assertEqual(tool_registry[old_name].parse_arguments(json.dumps(arguments))["series_id"], "series-a")
            result = execute_tool_call(ToolCall("legacy-model", old_name, json.dumps(arguments)),
                                       context, model_requested=True)
            self.assertEqual(result.error_type, "tool_not_exposed")
        schema = exposed["manage_topics"]["parameters"]
        self.assertEqual(schema["type"], "object")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["properties"]["action"]["enum"]),
                         {"list", "get", "create", "update", "remove"})
        self.assertFalse({"creator_id", "request_id", "sql", "path", "thread_id"}
                         .intersection(schema["properties"]))

    def test_valid_actions_preserve_exact_object_and_reference(self):
        reference = "a" * 64
        cases = [
            {"action": "list", "series_id": "s", "query": {"state": "queued", "offset": 20, "limit": 10}},
            {"action": "get", "topic_id": "original-topic"},
            {"action": "create", "series_id": "s", "topics": [{"title": "新选题", "reference_id": reference}]},
            {"action": "update", "topic_id": "original-topic", "changes": {"brief": "完整新场景"}, "expected_digest": reference},
            {"action": "update", "topic_id": "original-topic", "changes": {"brief": ""}, "expected_digest": reference},
            {"action": "remove", "topic_id": "original-topic"},
            {"action": "remove", "topic_id": "research-candidate", "candidate": {"batch_id": "b" * 32, "candidate_id": "c1"}},
        ]
        for arguments in cases:
            with self.subTest(action=arguments["action"], arguments=arguments):
                parsed = tool_registry["manage_topics"].parse_arguments(json.dumps(arguments))
                self.assertEqual(parsed["action"], arguments["action"])
                if "topic_id" in arguments:
                    self.assertEqual(parsed["topic_id"], arguments["topic_id"])
                if arguments["action"] == "create":
                    self.assertEqual(parsed["topics"][0]["reference_id"], reference)

    def test_action_mismatch_and_forged_fields_never_reach_transport(self):
        digest = "a" * 64
        invalid = [
            {}, {"action": "produce", "topic_id": "original"},
            {"action": "list"}, {"action": "get", "series_id": "s"},
            {"action": "list", "series_id": "s", "query": {"offset": -1}},
            {"action": "list", "series_id": "s", "query": {"limit": "20"}},
            {"action": "list", "series_id": "s", "query": {"limit": 101}},
            {"action": "create", "series_id": "s", "topics": []},
            {"action": "create", "series_id": "s", "topics": [{"title": "x", "topic_id": "overwrite"}]},
            {"action": "create", "series_id": "s", "topics": [{"title": "x"}], "changes": {"brief": "x"}},
            {"action": "get", "topic_id": "original", "creator_id": "foreign"},
            {"action": "update", "topic_id": "original", "changes": {"brief": "x"}},
            {"action": "update", "topic_id": "original", "changes": {}, "expected_digest": digest},
            {"action": "update", "topic_id": "original", "changes": {"status": "approved"}, "expected_digest": digest},
            {"action": "update", "topic_id": "original", "changes": {"title": " "}, "expected_digest": digest},
            {"action": "update", "topic_id": "original", "changes": {"brief": "x"}, "expected_digest": "guessed"},
            {"action": "remove", "topic_id": "original", "request_id": "caller-chosen"},
            {"action": "remove", "topic_id": "original", "candidate": {"batch_id": "invalid", "candidate_id": "c1"}},
            {"action": "remove", "topic_id": "original", "changes": {"title": "x"}},
        ]
        context = RuntimeContext(Path.cwd(), creator_id="account-a", allowed_tools=ACCOUNT_TOOLS)
        with patch("creatoros.integrations.studio.StudioClient.request") as request:
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    result = execute_tool_call(ToolCall("bad", "manage_topics", json.dumps(arguments)),
                                               context, model_requested=True)
                    self.assertEqual(result.error_type, "invalid_arguments", result.content)
            request.assert_not_called()

    def test_model_projection_keeps_edit_basis_and_separates_management(self):
        raw = {"action": "get", "topic": {"id": "original", "series_id": "s", "title": "原选题",
            "brief": "完整场景，不截断", "edit_digest": "d" * 64,
            "management_actions": ["get", "update", "remove"], "available_actions": ["view"],
            "cover_url": "/not-needed", "debug": "trace-only"}, "series_id": "s", "url": "/series/s"}
        original = deepcopy(raw)
        projected = project_model_data("manage_topics", raw)
        self.assertEqual(raw, original)
        self.assertEqual(projected["topic"]["edit_digest"], "d" * 64)
        self.assertEqual(projected["topic"]["brief"], "完整场景，不截断")
        self.assertEqual(projected["topic"]["management_actions"], ["get", "update", "remove"])
        self.assertEqual(projected["topic"]["available_actions"], ["view"])
        self.assertNotIn("debug", projected["topic"])
        self.assertNotIn("cover_url", projected["topic"])


class TopicToolHttpTests(unittest.TestCase):
    """Actual adapter -> original loopback API -> isolated SQLite and files."""

    setUp = topic_http.TopicManagementHttpTests.setUp
    tearDown = topic_http.TopicManagementHttpTests.tearDown

    def context(self):
        return RuntimeContext(self.root, studio_url=self.base, creator_id="review-lab",
            agent_session_id=self.sid, allowed_tools=ACCOUNT_TOOLS,
            session_file=self.app.state.chat.root / self.sid / "messages.json",
            user_request_id=uuid4().hex, archive_only_reads=True)

    def call(self, context, **arguments):
        return execute_tool_call(ToolCall(uuid4().hex, "manage_topics", json.dumps(arguments)),
                                 context, model_requested=True)

    def read(self, context, topic_id):
        result = self.call(context, action="get", topic_id=topic_id)
        self.assertFalse(result.is_error, result.content)
        return json.loads(result.content)["topic"]

    def test_actual_tool_create_get_update_remove_preserves_existing_history(self):
        context = self.context()
        historical_models = [ContentRun, ContentRevision, ContentAttempt]
        history = topic_http.rows(self.db, historical_models)
        existing = self.repo.get_topic("review-1")
        created = self.call(context, action="create", series_id="agent-notes",
                            topics=[{"title": "原来的选题", "brief": "原场景"}])
        self.assertFalse(created.is_error, created.content)
        topic_id = json.loads(created.content)["topic_ids"][0]
        original = self.read(context, topic_id)
        before_count = len(topic_http.rows(self.db, [Topic])["topics"])
        # The real args_model.model_dump includes changes.title=None. The adapter
        # must omit it from PATCH rather than clear/reject the untouched title.
        updated = self.call(context, action="update", topic_id=topic_id,
            changes={"brief": "上格安慰朋友，下格深夜自责。"}, expected_digest=original["edit_digest"])
        self.assertFalse(updated.is_error, updated.content)
        receipt = json.loads(updated.content)
        self.assertEqual(receipt["topic"]["id"], topic_id)
        self.assertEqual(receipt["changed_fields"], ["brief"])
        current = self.read(context, topic_id)
        self.assertEqual(current["title"], "原来的选题")
        self.assertEqual(current["brief"], "上格安慰朋友，下格深夜自责。")
        self.assertNotEqual(current["edit_digest"], original["edit_digest"])
        self.assertEqual(self.repo.get_topic(topic_id).brief, current["brief"])
        self.assertEqual(len(topic_http.rows(self.db, [Topic])["topics"]), before_count)
        listing = self.call(context, action="list", series_id="agent-notes", query={"state": "queued"})
        self.assertFalse(listing.is_error, listing.content)
        item = next(row for row in json.loads(listing.content)["items"] if row["id"] == topic_id)
        self.assertEqual(item["brief"], current["brief"])
        removed = self.call(context, action="remove", topic_id=topic_id)
        self.assertFalse(removed.is_error, removed.content)
        self.assertEqual(json.loads(removed.content)["status"], "deleted")
        self.assertIsNone(self.repo.get_topic(topic_id))
        self.assertEqual(topic_http.rows(self.db, historical_models), history)
        self.assertEqual(self.repo.get_topic("review-1").title, existing.title)
        self.assertEqual(self.producer.calls, 1)

    def test_successful_patch_lost_response_only_reads_receipt_across_restart(self):
        context = self.context()
        topic_id = "review-2"
        original = self.read(context, topic_id)
        arguments = {"action": "update", "topic_id": topic_id,
            "changes": {"brief": "真实保存，故障仅丢失响应"}, "expected_digest": original["edit_digest"]}
        before_topics = topic_http.rows(self.db, [Topic])
        before_receipts = topic_http.rows(self.db, [WriteReceipt])
        actual_request = StudioClient.request
        requests = []

        def discard_first_patch(client, method, path, **options):
            requests.append((method, path))
            data = actual_request(client, method, path, **options)
            if method == "PATCH":
                raise StudioClientError("注入：真实事务已提交，仅丢失传输响应", "studio_outcome_unknown")
            return data

        with patch.object(StudioClient, "request", discard_first_patch):
            lost = self.call(context, **arguments)
            self.assertEqual(lost.error_type, "studio_outcome_unknown", lost.content)
            self.assertEqual(self.repo.get_topic(topic_id).brief, arguments["changes"]["brief"])
            self.assertEqual(len(topic_http.rows(self.db, [Topic])["topics"]), len(before_topics["topics"]))
            self.assertEqual(len(topic_http.rows(self.db, [WriteReceipt])["write_receipts"]),
                             len(before_receipts["write_receipts"]) + 1)
            markers = list(context.session_file.with_suffix(".actions").glob("*.json"))
            self.assertEqual(len(markers), 1)
            marker_bytes = markers[0].read_bytes()
            request_id = json.loads(marker_bytes)["request_id"]
            receipt_path = f"/api/topics/{topic_id}/edit-receipts/{request_id}"
            recovered = self.call(context, **arguments)
            self.assertFalse(recovered.is_error, recovered.content)
            self.assertTrue(json.loads(recovered.content)["deduplicated"])
            self.assertEqual(json.loads(recovered.content)["topic"]["id"], topic_id)
            self.assertEqual(requests, [("PATCH", f"/api/topics/{topic_id}"), ("GET", receipt_path)])
            after_recovery = topic_http.rows(self.db, [Topic, WriteReceipt])

            # Actually close/recreate the HTTP host and dispose its SQLite
            # connections; restore from the same disk marker and chat identity.
            chat_root = self.app.state.chat.root
            self.client.close()
            self.host.__exit__(None, None, None)
            self.db.engine.dispose()
            self.app = create_app(database=self.db, run_service=self.runs, chat_root=chat_root)
            self.host = serve(self.app)
            self.base = self.host.__enter__()
            self.client = httpx.Client(base_url=self.base, timeout=15, trust_env=False)
            restored_context = replace(context, studio_url=self.base)
            restored = self.call(restored_context, **arguments)
            self.assertFalse(restored.is_error, restored.content)
            self.assertTrue(json.loads(restored.content)["deduplicated"])
            self.assertEqual(requests[-1], ("GET", receipt_path))
            self.assertEqual(sum(method == "PATCH" for method, _ in requests), 1)
            self.assertEqual(markers[0].read_bytes(), marker_bytes)
            self.assertEqual(topic_http.rows(self.db, [Topic, WriteReceipt]), after_recovery)
        self.assertEqual(self.producer.calls, 1)


if __name__ == "__main__":
    unittest.main()
