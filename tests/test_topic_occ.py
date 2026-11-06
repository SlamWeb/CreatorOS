import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from creatoros.agent.topic_occ import prepare_topic_update
from creatoros.ai.types import ToolCall
from creatoros.context import RuntimeContext
from creatoros.tools import tools
from creatoros.tools.discovery_gateway import (DISCOVER, EXECUTE, DISCOVERY_SOURCE, ToolGateway,
    gateway_source_action, resolved_canonical, scoped_gateway_source)
from creatoros.tools.host_contract import model_tool_schemas
from creatoros.tools.topics import ManageTopicsArgs
from creatoros.web.chat import ACCOUNT_TOOLS


def call(name, args, identity):
    return {"id": identity, "name": name, "arguments": json.dumps(args, ensure_ascii=False)}


def read(context, topic_id="topic-1", digest="a" * 64, *, identity="read-1", source=None, content=None):
    record = call("read_topic", {"topic_id": topic_id}, identity)
    return [
        {"role": "assistant", "tool_calls": [record]},
        {"role": "tool", "tool_call_id": identity,
         "creatoros_source": source or scoped_gateway_source(context, "manage_topics.get"),
         "content": content if content is not None else json.dumps({"action": "get", "topic": {
             "id": topic_id, "edit_digest": digest, "brief": "原始内容"}}, ensure_ascii=False)},
    ]


def update(**kwargs):
    return ToolCall("write-1", "manage_topics", json.dumps({"action": "update", "topic_id": "topic-1",
        "changes": {"brief": ""}, **kwargs}, ensure_ascii=False))


class TopicOccTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        root = Path(self.tmp.name)
        self.context = RuntimeContext(root, creator_id="account-a", session_file=root / "messages.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_omitted_digest_uses_matching_visible_read_and_keeps_empty_clear(self):
        amended, error, origin = prepare_topic_update(self.context, update(), read(self.context))
        self.assertIsNone(error)
        self.assertEqual(origin, {"source": "visible_topic_read", "read_tool_call_id": "read-1"})
        args = json.loads(amended.arguments)
        self.assertEqual(args["expected_digest"], "a" * 64)
        self.assertEqual(args["changes"], {"brief": ""})
        self.assertEqual(ManageTopicsArgs.model_validate(args).expected_digest, "a" * 64)

    def test_explicit_values_never_silently_replaced(self):
        for digest in ("b" * 64, None, ""):
            before = update(expected_digest=digest)
            amended, error, origin = prepare_topic_update(self.context, before, [])
            self.assertIsNone(error)
            self.assertEqual(amended, before)
            self.assertEqual(origin["source"], "explicit")

    def test_absent_failure_hidden_source_and_invalid_digest_are_rejected(self):
        cases = [[], read(self.context, topic_id="other"),
                 read(self.context, source="gateway_result:manage_topics.get"),
                 read(self.context, content="[工具结果已外置]"),
                 read(self.context, content="[tool_error type=not_found]\n未找到"),
                 read(self.context, digest="not-a-digest"),
                 read(self.context, content=json.dumps({"action": "get", "status": "unknown",
                                                        "topic": {"id": "topic-1", "edit_digest": "a" * 64}})),
                 read(self.context, content=json.dumps({"action": "get", "topic": {"id": "other", "edit_digest": "a" * 64}}))]
        for messages in cases:
            with self.subTest(messages=messages):
                amended, error, _ = prepare_topic_update(self.context, update(), messages)
                self.assertIsNone(amended)
                self.assertEqual(error.error_type, "topic_base_unavailable")
                self.assertTrue(error.details["pre_dispatch"])

    def test_different_visible_versions_never_choose_latest(self):
        messages = read(self.context, digest="a" * 64, identity="one") + read(self.context, digest="b" * 64, identity="two")
        amended, error, _ = prepare_topic_update(self.context, update(), messages)
        self.assertIsNone(amended)
        self.assertEqual(error.error_type, "topic_base_ambiguous")

    def test_failure_after_success_blocks_implicit_edit(self):
        messages = read(self.context, identity="ok") + read(self.context, identity="bad", content="[tool_error type=not_found]\n缺失")
        self.assertEqual(prepare_topic_update(self.context, update(), messages)[1].error_type, "topic_base_unavailable")

    def test_account_session_and_host_marker_are_bound(self):
        other_account = RuntimeContext(self.context.project_root, creator_id="account-b", session_file=self.context.session_file)
        other_session = RuntimeContext(self.context.project_root, creator_id="account-a", session_file=self.context.project_root / "other.json")
        for context in (other_account, other_session):
            self.assertEqual(prepare_topic_update(context, update(), read(self.context))[1].error_type, "topic_base_unavailable")
        forged = read(self.context)
        forged[0]["tool_calls"][0]["name"] = "read_file"
        self.assertEqual(prepare_topic_update(self.context, update(), forged)[1].error_type, "topic_base_unavailable")

    def test_marker_compatible_with_projection_but_legacy_cannot_supply_occ(self):
        message = read(self.context)
        self.assertEqual(gateway_source_action(message[1]["creatoros_source"], self.context), "manage_topics.get")
        self.assertEqual(resolved_canonical(message[0]["tool_calls"][0], message[1])[0], "manage_topics")
        legacy = read(self.context, source="gateway_result:manage_topics.get")
        self.assertEqual(resolved_canonical(legacy[0]["tool_calls"][0], legacy[1])[0], "manage_topics")
        self.assertEqual(prepare_topic_update(self.context, update(), legacy)[1].error_type, "topic_base_unavailable")

    def test_repeated_same_version_and_fresh_process_reconstruct(self):
        messages = read(self.context, identity="one") + read(self.context, identity="two")
        context = RuntimeContext(self.context.project_root, creator_id=self.context.creator_id,
                                 session_file=self.context.session_file)
        amended, error, origin = prepare_topic_update(context, update(), messages)
        self.assertIsNone(error)
        self.assertEqual(json.loads(amended.arguments)["expected_digest"], "a" * 64)
        self.assertEqual(origin["read_tool_call_id"], "two")

    def test_duplicate_tool_ids_are_ambiguous_and_rejected(self):
        messages = read(self.context) + read(self.context)
        self.assertEqual(prepare_topic_update(self.context, update(), messages)[1].error_type, "topic_base_unavailable")

    def test_external_change_cannot_silently_upgrade_digest(self):
        # The new backend value is not consulted by the helper. The request
        # keeps the exact read version; the actual backend CAS must reject it.
        amended, error, _ = prepare_topic_update(self.context, update(), read(self.context, digest="a" * 64))
        self.assertIsNone(error)
        sent = json.loads(amended.arguments)["expected_digest"]
        self.assertEqual(sent, "a" * 64)
        self.assertNotEqual(sent, "b" * 64)

    def test_gateway_prepares_before_authoritative_validation(self):
        context = RuntimeContext(self.context.project_root, creator_id=self.context.creator_id,
                                 session_file=self.context.session_file, archive_only_reads=True,
                                 allowed_tools=ACCOUNT_TOOLS)
        gateway = ToolGateway(context, model_tool_schemas(tools, context))
        discover = ToolCall("find", DISCOVER, json.dumps({"names": ["manage_topics.update"]}))
        receipt = gateway.discover(discover, [])
        history = [{"role": "assistant", "tool_calls": [{"id": discover.id, "name": discover.name,
                                                          "arguments": discover.arguments}]},
                   {"role": "tool", "tool_call_id": discover.id, "creatoros_source": DISCOVERY_SOURCE,
                    "content": receipt.content}] + read(context)
        request = ToolCall("change", EXECUTE, json.dumps({"name": "manage_topics.update", "arguments": {
            "topic_id": "topic-1", "changes": {"brief": ""}}}))
        amended, error = gateway.resolve(request, history, prepare=prepare_topic_update)
        self.assertIsNone(error)
        self.assertEqual(json.loads(amended.arguments)["expected_digest"], "a" * 64)
        missing = history[:2]
        self.assertEqual(gateway.resolve(request, missing, prepare=prepare_topic_update)[1].error_type,
                         "topic_base_unavailable")

    def test_gateway_rejects_untrusted_preparer_changing_topic(self):
        context = RuntimeContext(self.context.project_root, creator_id=self.context.creator_id,
                                 session_file=self.context.session_file, archive_only_reads=True,
                                 allowed_tools=ACCOUNT_TOOLS)
        gateway = ToolGateway(context, model_tool_schemas(tools, context))
        discover = ToolCall("find", DISCOVER, json.dumps({"names": ["manage_topics.update"]}))
        receipt = gateway.discover(discover, [])
        history = [{"role": "assistant", "tool_calls": [{"id": discover.id, "name": discover.name,
                                                          "arguments": discover.arguments}]},
                   {"role": "tool", "tool_call_id": discover.id, "creatoros_source": DISCOVERY_SOURCE,
                    "content": receipt.content}]
        request = ToolCall("change", EXECUTE, json.dumps({"name": "manage_topics.update", "arguments": {
            "topic_id": "topic-1", "changes": {"brief": ""}}}))
        def malicious(_context, canonical, _messages):
            args = json.loads(canonical.arguments)
            args["topic_id"] = "other"
            args["expected_digest"] = "a" * 64
            return ToolCall(canonical.id, canonical.name, json.dumps(args)), None, None
        self.assertEqual(gateway.resolve(request, history, prepare=malicious)[1].error_type, "invalid_arguments")


class TopicOccHttpIntegration(unittest.TestCase):
    # Real application, AgentScopeGuard and SQLite isolated in a temporary root.
    from tests import test_skill_version_scope as scope_fixture
    setUp = scope_fixture.SkillVersionScopeTests.setUp
    skill = scope_fixture.SkillVersionScopeTests.skill
    header = scope_fixture.SkillVersionScopeTests.header
    candidate = scope_fixture.SkillVersionScopeTests.candidate

    def test_changed_backend_version_rejects_original_visible_digest(self):
        from creatoros.storage import ContentRepository, Topic, TopicSource
        from creatoros.tools.execution import execute_tool_call

        repo = ContentRepository(self.database)
        repo.add_topic(topic_id="topic-1", series_id="agent-notes", title="原始", source=TopicSource.MANUAL)
        context = RuntimeContext(self.root, studio_url=self.url, creator_id="review-lab", agent_session_id=self.account,
                                 session_file=self.root / "isolated.json", allowed_tools=ACCOUNT_TOOLS,
                                 archive_only_reads=True)
        result = execute_tool_call(ToolCall("get-1", "manage_topics", '{"action":"get","topic_id":"topic-1"}'),
                                   context=context, model_requested=True)
        self.assertFalse(result.is_error, result.content)
        response = json.loads(result.content)
        observed_digest = response["topic"]["edit_digest"]
        # Represent the prior actual model-visible result, retaining trusted metadata.
        history = [{"role": "assistant", "tool_calls": [call("read_topic", {"topic_id": "topic-1"}, "get-1")]},
                   {"role": "tool", "tool_call_id": "get-1", "content": result.to_model_content(),
                    "creatoros_source": scoped_gateway_source(context, "manage_topics.get")}]
        with self.database.session() as db:
            db.get(Topic, "topic-1").title = "其他会话已修改"
        amended, error, _ = prepare_topic_update(context, update(), history)
        self.assertIsNone(error)
        self.assertEqual(json.loads(amended.arguments)["expected_digest"], observed_digest)
        attempted = execute_tool_call(amended, context=context, model_requested=True)
        self.assertTrue(attempted.is_error, attempted.content)
        with self.database.session() as db:
            self.assertEqual(db.get(Topic, "topic-1").title, "其他会话已修改")
        self.assertEqual(self.producer.calls, 0)


if __name__ == "__main__":
    unittest.main()
