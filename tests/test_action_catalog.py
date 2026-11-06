import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from creatoros.ai.context import estimate_tokens
from creatoros.context import RuntimeContext
from creatoros.tools import tool_registry, tools
from creatoros.tools.action_catalog import build_action_catalog, build_direct_read_schemas, resolve_direct_read
from creatoros.tools.host_contract import bind_account_arguments, model_tool_schemas
from creatoros.web.chat import ACCOUNT_TOOLS


class ActionCatalogTests(unittest.TestCase):
    def setUp(self):
        self.scope = RuntimeContext(Path.cwd(), creator_id="fixture-account", archive_only_reads=True,
                                    allowed_tools=ACCOUNT_TOOLS)
        self.schemas = model_tool_schemas(tools, self.scope)
        self.catalog = build_action_catalog(self.schemas)

    def test_every_visible_tool_and_action_is_discoverable(self):
        self.assertEqual({v["name"] for v in self.catalog.values()},
                         {s["function"]["name"] for s in self.schemas})
        self.assertEqual(len(self.catalog), 70)
        for source in self.schemas:
            f = source["function"]
            p = f["parameters"]
            mapping = p.get("discriminator", {}).get("mapping", {})
            enum = p.get("properties", {}).get("action", {}).get("enum", [])
            for action in mapping or enum:
                self.assertIn(f"{f['name']}.{action}", self.catalog)
        for entry in self.catalog.values():
            self.assertTrue(entry["label"])
            self.assertTrue(entry["schema"]["function"]["description"])
            Draft202012Validator.check_schema(entry["schema"]["function"]["parameters"])
            if entry["action"] is not None:
                params = entry["schema"]["function"]["parameters"]
                self.assertEqual(params["properties"]["action"]["const"], entry["action"])
                self.assertIn("action", params["required"])

    def test_action_fields_preserve_critical_evidence_and_actual_parser(self):
        samples = {
            "manage_topics.list": {"action": "list", "series_id": "series-1", "query": {"state": "queued", "limit": 5}},
            "manage_topics.get": {"action": "get", "topic_id": "topic-1", "production_request_id": "receipt-1"},
            "manage_topics.update": {"action": "update", "topic_id": "topic-1", "expected_digest": "a" * 64,
                                     "changes": {"brief": "原文"}},
            "manage_topics.remove": {"action": "remove", "topic_id": "topic-1",
                                     "candidate": {"batch_id": "a" * 32, "candidate_id": "candidate-1"}},
            "manage_media_collection.list": {"action": "list", "include_sources": True, "limit": 10},
            "manage_media_collection.export": {"action": "export", "collection_id": "collection-1",
                                               "expected_version": 1, "snapshot_digest": "b" * 64},
            "manage_skill_library.get": {"action": "get"},
            "manage_plan.get": {"action": "get", "plan_id": "plan-1"},
            "start_content_run.start": {"action": "start", "topic_ids": ["topic-1"]},
        }
        for action_id, arguments in samples.items():
            entry = self.catalog[action_id]
            with self.subTest(action=action_id):
                schema = entry["schema"]["function"]["parameters"]
                Draft202012Validator(schema).validate(arguments)
                bound = bind_account_arguments(entry["name"], json.dumps(arguments), self.scope)
                tool_registry[entry["name"]].parse_arguments(bound)
                other = dict(arguments, action="unexpected")
                self.assertFalse(Draft202012Validator(schema).is_valid(other))

    def test_action_schemas_are_smaller_and_source_is_not_modified(self):
        source = json.dumps(self.schemas, ensure_ascii=False, sort_keys=True)
        build_action_catalog(self.schemas)
        self.assertEqual(source, json.dumps(self.schemas, ensure_ascii=False, sort_keys=True))
        full = {x["function"]["name"]: x for x in self.schemas}
        for action_id in ("manage_topics.get", "manage_topics.list", "manage_plan.get", "start_content_run.start"):
            entry = self.catalog[action_id]
            self.assertLess(estimate_tokens(entry["schema"]), estimate_tokens(full[entry["name"]]))

    def test_all_branch_refs_exist_and_source_constraints_remain(self):
        for entry in self.catalog.values():
            schema = entry["schema"]["function"]["parameters"]
            definitions = schema.get("$defs", {})
            def visit(value):
                if isinstance(value, dict):
                    ref = value.get("$ref")
                    if isinstance(ref, str) and ref.startswith("#/$defs/"):
                        self.assertIn(ref.rsplit("/", 1)[-1], definitions)
                    for child in value.values():
                        visit(child)
                elif isinstance(value, list):
                    for child in value:
                        visit(child)
            with self.subTest(action=entry["id"]):
                visit(schema)

    def test_unbound_schema_keeps_creator_id_when_present(self):
        context = RuntimeContext(Path.cwd(), allowed_tools=ACCOUNT_TOOLS)
        catalog = build_action_catalog(model_tool_schemas(tools, context))
        params = catalog["manage_media_collection.list"]["schema"]["function"]["parameters"]
        self.assertIn("creator_id", params["properties"])
        self.assertIn("creator_id", params["required"])

    def test_direct_read_aliases_are_fixed_and_follow_visible_scope(self):
        direct = build_direct_read_schemas(self.catalog)
        self.assertEqual([x["function"]["name"] for x in direct], ["read_topic", "list_topics", "get_creator_tasks"])
        for schema in direct[:2]:
            self.assertNotIn("action", schema["function"]["parameters"]["properties"])
        for alias, payload, action in [("read_topic", {"topic_id": "topic-1"}, "get"),
                                       ("list_topics", {"series_id": "series-1"}, "list")]:
            name, arguments = resolve_direct_read(alias, payload, self.catalog)
            self.assertEqual(name, "manage_topics")
            self.assertEqual(arguments["action"], action)
            tool_registry[name].parse_arguments(json.dumps(arguments))
            with self.assertRaises(ValueError):
                resolve_direct_read(alias, {**payload, "action": "remove"}, self.catalog)
        restricted = build_action_catalog([s for s in self.schemas if s["function"]["name"] == "get_creator_tasks"])
        self.assertEqual([s["function"]["name"] for s in build_direct_read_schemas(restricted)], ["get_creator_tasks"])
        with self.assertRaises(ValueError):
            resolve_direct_read("read_topic", {"topic_id": "topic-1"}, restricted)


class DirectReadScopeIntegration(unittest.TestCase):
    # Isolated SQLite and local HTTP/AgentScopeGuard from existing integration fixtures.
    from tests import test_skill_version_scope as scope_fixture
    setUp = scope_fixture.SkillVersionScopeTests.setUp
    skill = scope_fixture.SkillVersionScopeTests.skill
    header = scope_fixture.SkillVersionScopeTests.header
    candidate = scope_fixture.SkillVersionScopeTests.candidate

    def test_alias_dispatch_preserves_real_account_scope(self):
        from creatoros.ai.types import ToolCall
        from creatoros.storage import ContentRepository, TopicSource
        from creatoros.tools.execution import execute_tool_call

        repo = ContentRepository(self.database)
        repo.add_topic(topic_id="own-topic", series_id="agent-notes", title="自己的资料", source=TopicSource.MANUAL)
        repo.add_topic(topic_id="foreign-topic", series_id="other-series", title="另一个账号", source=TopicSource.MANUAL)
        context = RuntimeContext(self.root, studio_url=self.url, creator_id="review-lab", agent_session_id=self.account,
                                 allowed_tools=ACCOUNT_TOOLS, archive_only_reads=True)
        schemas = model_tool_schemas(tools, context)
        catalog = build_action_catalog(schemas)
        results = []
        for topic_id in ("own-topic", "foreign-topic"):
            name, arguments = resolve_direct_read("read_topic", {"topic_id": topic_id}, catalog)
            results.append(execute_tool_call(ToolCall("direct-" + topic_id, name, json.dumps(arguments)),
                                             context=context, model_requested=True))
        self.assertFalse(results[0].is_error, results[0].content)
        self.assertIn("自己的资料", results[0].content)
        self.assertTrue(results[1].is_error)
        self.assertNotIn("另一个账号", results[1].content)
        self.assertEqual(self.producer.calls, 0)


if __name__ == "__main__":
    unittest.main()
