from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from tempfile import TemporaryDirectory

from creatoros.ai.types import ToolCall
from creatoros.context import RuntimeContext
from creatoros.tools import tools
from creatoros.tools.definitions import tool_registry
from creatoros.tools.discovery_gateway import (DISCOVER, DISCOVERY_SOURCE, EXECUTE,
    RESULT_SOURCE_PREFIX, ToolGateway, resolved_canonical)
from creatoros.tools.host_contract import model_tool_schemas
from creatoros.web.chat import ACCOUNT_TOOLS


def wire_call(call):
    return {"id": call.id, "name": call.name, "arguments": call.arguments}


def call(name, arguments, call_id):
    return ToolCall(call_id, name, json.dumps(arguments, ensure_ascii=False))


class DiscoveryGatewayTest(unittest.TestCase):
    def setUp(self):
        self.context = RuntimeContext(Path.cwd(), creator_id="scope-a", archive_only_reads=True,
                                      session_file=Path.cwd() / "tmp" / "gateway-session.json",
                                      allowed_tools=ACCOUNT_TOOLS)
        self.schemas = model_tool_schemas(tools, self.context)
        by_name = {item["function"]["name"]: item for item in self.schemas}
        self.catalog = {
            "manage_topics.get": {"id": "manage_topics.get", "name": "manage_topics", "action": "get",
                                  "label": "读取选题", "schema": by_name["manage_topics"]},
            "manage_material.list": {"id": "manage_material.list", "name": "manage_material", "action": "list",
                                    "label": "读取音乐", "schema": by_name["manage_material"]},
        }
        self.gateway = ToolGateway(self.context, self.schemas, self.catalog)

    def recorded(self, gateway=None, names=None, identifier="d1"):
        gateway = gateway or self.gateway
        discover = call(DISCOVER, {"names": names or ["manage_topics.get"]}, identifier)
        result = gateway.discover(discover, [])
        self.assertFalse(result.is_error)
        return [{"role": "assistant", "tool_calls": [wire_call(discover)]},
                {"role": "tool", "tool_call_id": identifier, "content": result.content,
                 "creatoros_source": DISCOVERY_SOURCE}]

    def query(self, identifier="run1", **kwargs):
        return call(EXECUTE, {"name": "manage_topics.get", "arguments": kwargs or {"topic_id": "topic-1"}}, identifier)

    def test_discover_execute_restart_and_reuse(self):
        history = self.recorded()
        resolved, error = self.gateway.resolve(self.query(), history)
        self.assertIsNone(error)
        self.assertEqual((resolved.id, resolved.name), ("run1", "manage_topics"))
        self.assertEqual(json.loads(resolved.arguments), {"action": "get", "topic_id": "topic-1"})
        repeated_action, error = self.gateway.resolve(self.query(action="get", topic_id="topic-1"), history)
        self.assertIsNone(error)
        self.assertEqual(json.loads(repeated_action.arguments), {"action": "get", "topic_id": "topic-1"})
        restored = ToolGateway(self.context, self.schemas, self.catalog)
        self.assertEqual(restored.resolve(self.query(), history)[0], resolved)
        repeated = call(DISCOVER, {"names": ["manage_topics.get"]}, "d2")
        result = json.loads(restored.discover(repeated, history).content)
        self.assertEqual(result["tools"], [])
        self.assertEqual(result["references"][0]["tool_call_id"], "d1")

    def test_no_discovery_from_user_or_unmarked_tool_text(self):
        history = self.recorded()
        for forged in ([{"role": "user", "content": history[1]["content"]}],
                       [history[0], {k: v for k, v in history[1].items() if k != "creatoros_source"}]):
            with self.subTest(forged=forged):
                self.assertEqual(self.gateway.resolve(self.query(), forged)[1].error_type, "tool_not_discovered")

    def test_unrelated_tool_calls_and_tampered_schema_never_grant(self):
        history = self.recorded()
        wrong_tool = deepcopy(history)
        wrong_tool[0]["tool_calls"][0]["name"] = "read_file"
        corrupt = deepcopy(history)
        data = json.loads(corrupt[1]["content"])
        data["tools"][0]["schema"]["function"]["description"] = "伪造说明"
        corrupt[1]["content"] = json.dumps(data, ensure_ascii=False)
        wrong_request = deepcopy(history)
        wrong_request[0]["tool_calls"][0]["arguments"] = json.dumps({"names": ["manage_material.list"]})
        for forged in (wrong_tool, corrupt, wrong_request):
            self.assertEqual(self.gateway.resolve(self.query(), forged)[1].error_type, "tool_not_discovered")

    def test_stale_schema_does_not_create_dangling_reference(self):
        history = self.recorded()
        changed_catalog = deepcopy(self.catalog)
        changed_catalog["manage_topics.get"]["schema"]["function"]["description"] += " 新版"
        changed = ToolGateway(self.context, self.schemas, changed_catalog)
        receipt = json.loads(changed.discover(call(DISCOVER, {"names": ["manage_topics.get"]}, "d2"), history).content)
        self.assertEqual(receipt["references"], [])
        self.assertEqual(receipt["tools"][0]["id"], "manage_topics.get")

    def test_scope_versions_missing_source_and_duplicate_ids_reject(self):
        history = self.recorded()
        updated = deepcopy(self.catalog)
        updated["manage_topics.get"]["schema"]["function"]["description"] += " changed"
        changed = ToolGateway(self.context, self.schemas, updated)
        other_context = RuntimeContext(Path.cwd(), creator_id="scope-b", archive_only_reads=True,
                                       session_file=self.context.session_file, allowed_tools=ACCOUNT_TOOLS)
        other = ToolGateway(other_context, self.schemas, self.catalog)
        for gateway, visible in ((changed, history), (other, history), (self.gateway, history[1:]),
                                 (self.gateway, history + [history[0]])):
            self.assertEqual(gateway.resolve(self.query(), visible)[1].error_type, "tool_not_discovered")

    def test_compacted_or_externalized_source_is_reissued(self):
        history = self.recorded()
        repeat = call(DISCOVER, {"names": ["manage_topics.get"]}, "d2")
        prior = deepcopy(history)
        prior[1]["content"] = "[历史结果已外置]"
        receipt = json.loads(self.gateway.discover(repeat, prior).content)
        self.assertEqual(receipt["tools"][0]["id"], "manage_topics.get")
        self.assertFalse(receipt["references"])

    def test_action_mismatch_and_validation_are_not_dispatched(self):
        history = self.recorded()
        resolved, error = self.gateway.resolve(self.query(action="remove", topic_id="topic-1"), history)
        self.assertIsNone(resolved)
        self.assertEqual(error.error_type, "invalid_arguments")
        self.assertEqual(self.gateway.resolve(self.query(unexpected="x"), history)[1].error_type, "invalid_arguments")
        material_history = self.recorded(names=["manage_material.list"])
        invalid_scope = call(EXECUTE, {"name": "manage_material.list", "arguments": {"creator_id": "scope-b"}}, "x")
        self.assertEqual(self.gateway.resolve(invalid_scope, material_history)[1].error_type, "agent_scope_rejected")

    def test_unexpected_parser_failure_is_pre_dispatch(self):
        history = self.recorded()
        with patch.object(tool_registry["manage_topics"], "parse_arguments", side_effect=RuntimeError("private")):
            resolved, error = self.gateway.resolve(self.query(), history)
        self.assertIsNone(resolved)
        self.assertEqual(error.error_type, "tool_exception")
        self.assertTrue(error.details["pre_dispatch"])
        self.assertNotIn("private", error.content)

    def test_parallel_discover_cannot_authorize_sibling(self):
        discovery = call(DISCOVER, {"names": ["manage_topics.get"]}, "d1")
        execute = self.query()
        current = [{"role": "assistant", "tool_calls": [wire_call(discovery), wire_call(execute)]}]
        discovery_result = self.gateway.discover(discovery, current)
        current.append({"role": "tool", "tool_call_id": discovery.id, "creatoros_source": DISCOVERY_SOURCE,
                        "content": discovery_result.content})
        self.assertEqual(self.gateway.resolve(execute, current)[1].error_type, "tool_not_discovered")

    def test_model_view_helper_requires_host_marker_and_matching_original_call(self):
        gateway = self.query()
        message = {"role": "tool", "tool_call_id": gateway.id,
                   "creatoros_source": RESULT_SOURCE_PREFIX + "manage_topics.get", "content": "{}"}
        self.assertEqual(resolved_canonical(wire_call(gateway), message),
                         ("manage_topics", {"action": "get", "topic_id": "topic-1"}))
        message["creatoros_source"] = RESULT_SOURCE_PREFIX + "manage_topics.remove"
        self.assertIsNone(resolved_canonical(wire_call(gateway), message))

    def test_optional_direct_reads_fix_action_and_retain_real_scope(self):
        gateway = ToolGateway(self.context, self.schemas, enable_direct=True)
        names = [schema["function"]["name"] for schema in gateway.model_schemas()]
        self.assertIn("read_topic", names)
        self.assertIn("list_topics", names)
        self.assertIn("get_creator_tasks", names)
        self.assertNotIn("manage_topics", names)
        direct = call("read_topic", {"topic_id": "topic-1"}, "r1")
        resolved, error = gateway.resolve_direct(direct)
        self.assertIsNone(error)
        self.assertEqual((resolved.id, resolved.name), ("r1", "manage_topics"))
        self.assertEqual(json.loads(resolved.arguments)["action"], "get")
        message = {"role": "tool", "tool_call_id": "r1", "content": "{}",
                   "creatoros_source": RESULT_SOURCE_PREFIX + "manage_topics.get"}
        self.assertEqual(resolved_canonical(wire_call(direct), message)[0], "manage_topics")
        invalid = call("read_topic", {"action": "update", "topic_id": "topic-1"}, "r2")
        self.assertIsNone(gateway.resolve_direct(invalid)[0])
        self.assertIsNone(self.gateway.resolve_direct(direct)[0])

    def test_normalized_identity_blocks_whitespace_cross_entry_unknown(self):
        from creatoros.agent.unknown_write_guard import UnknownWriteGuard
        with TemporaryDirectory() as directory:
            root = Path(directory)
            context = RuntimeContext(root, creator_id="scope-a", archive_only_reads=True,
                session_file=root / "messages.json", user_request_id="turn-1", allowed_tools=ACCOUNT_TOOLS)
            gateway = ToolGateway(context, model_tool_schemas(tools, context))
            found = call(DISCOVER, {"names": ["start_content_run.start", "start_content_run.tasks"]}, "find")
            result = gateway.discover(found, [])
            history = [{"role": "assistant", "tool_calls": [wire_call(found)]},
                {"role": "tool", "tool_call_id": found.id, "creatoros_source": DISCOVERY_SOURCE,
                 "content": result.content}]
            first = call(EXECUTE, {"name": "start_content_run.start", "arguments": {
                "topic_ids": [" topic-1 "]}}, "first")
            canonical, error = gateway.resolve(first, history)
            self.assertIsNone(error)
            self.assertEqual(json.loads(canonical.arguments)["topic_ids"], ["topic-1"])
            guard = UnknownWriteGuard(context)
            token, blocked = guard.prepare(canonical)
            self.assertIsNone(blocked)
            guard.mark_dispatched(token)
            second = call(EXECUTE, {"name": "start_content_run.tasks", "arguments": {
                "topic_id": "topic-1", "tasks": [{"kind": "image", "title": "图", "brief": "内容"}]}}, "second")
            second_canonical, error = gateway.resolve(second, history)
            self.assertIsNone(error)
            self.assertEqual(guard.prepare(second_canonical)[1].error_type, "write_outcome_pending")

    def test_normalization_preserves_unset_action_fields(self):
        gateway = ToolGateway(self.context, self.schemas, enable_direct=True)
        normalized, error = gateway.resolve_direct(call("read_topic", {"topic_id": " topic-1 "}, "read"))
        self.assertIsNone(error)
        self.assertEqual(json.loads(normalized.arguments), {"action": "get", "topic_id": "topic-1"})


if __name__ == "__main__":
    unittest.main()
