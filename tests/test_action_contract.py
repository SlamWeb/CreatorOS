"""Small DTO regressions; provider acceptance is checked by a separate live probe."""
from copy import deepcopy
import inspect
import json
from pathlib import Path
import unittest

from pydantic import ValidationError

from creatoros.ai.types import ToolCall
from creatoros.context import RuntimeContext
from creatoros.tools import execute_tool_call
from creatoros.tools.definitions import tool_registry
from creatoros.tools.host_contract import (
    AccountArgumentRejected, bind_account_arguments, model_tool_schemas,
)
from creatoros.tools.staged_production import ManageSkillLibraryArgs, _preserve_defaults


class SkillActionContractTests(unittest.TestCase):
    def setUp(self):
        self.tool = tool_registry["manage_skill_library"]
        self.context = RuntimeContext(Path.cwd(), creator_id="account-a")

    def test_schema_branches_and_parse_share_exact_action_fields(self):
        schema = self.tool.to_schema()["function"]["parameters"]
        self.assertEqual(schema, ManageSkillLibraryArgs.model_json_schema())
        self.assertEqual(schema["type"], "object")
        self.assertEqual(schema["discriminator"]["propertyName"], "action")
        samples = [
            ({"action": "get"}, {"action"}, {"action", "series_id"}),
            ({"action": "add", "skill_id": "skill-a"},
             {"action", "skill_id"}, {"action", "skill_id"}),
            ({"action": "remove", "skill_id": "skill-a", "expected_version": 1},
             {"action", "skill_id", "expected_version"}, {"action", "skill_id", "expected_version"}),
            ({"action": "set_pool", "series_id": "column-a", "expected_version": 1, "skill_ids": []},
             {"action", "series_id", "expected_version", "skill_ids"},
             {"action", "series_id", "expected_version", "skill_ids"}),
        ]
        for arguments, required, fields in samples:
            with self.subTest(action=arguments["action"]):
                reference = schema["discriminator"]["mapping"][arguments["action"]]
                branch = schema["$defs"][reference.rsplit("/", 1)[-1]]
                self.assertEqual(set(branch["required"]), required)
                self.assertEqual(set(branch["properties"]), fields | {"creator_id"})
                self.assertIs(branch["additionalProperties"], False)
                parsed = self.tool.parse_arguments(json.dumps(arguments))
                self.assertEqual(parsed, ManageSkillLibraryArgs.model_validate(arguments).model_dump())
                for name, value in arguments.items():
                    self.assertEqual(parsed[name], value)
                inspect.signature(self.tool.execute).bind(**parsed)
                for field in required - {"action"}:
                    missing = {key: value for key, value in arguments.items() if key != field}
                    with self.assertRaises(ValidationError):
                        self.tool.parse_arguments(json.dumps(missing))

    def test_b23_add_with_expected_version_is_schema_and_host_invalid(self):
        # The first B23 call sent expected_version=3 with add; keep it rejected.
        arguments = {"action": "add", "skill_id": "bench-extra--b0fb68304f36ac55", "expected_version": 3}
        schema = ManageSkillLibraryArgs.model_json_schema()
        reference = schema["discriminator"]["mapping"]["add"]
        branch = schema["$defs"][reference.rsplit("/", 1)[-1]]
        self.assertNotIn("expected_version", branch["properties"])
        self.assertIs(branch["additionalProperties"], False)
        result = execute_tool_call(ToolCall("b23", self.tool.name, json.dumps(arguments)),
                                   self.context, model_requested=True)
        self.assertEqual(result.error_type, "invalid_arguments")
        self.assertTrue(result.details["pre_dispatch"])
        self.assertEqual(result.details["validation_errors"][0]["type"], "extra_forbidden")

    def test_extra_null_legacy_defaults_and_wrong_action_are_rejected(self):
        invalid = [
            {"action": "add", "skill_id": "skill-a", "series_id": None},
            {"action": "add", "skill_id": "skill-a", "unknown": None},
            {"action": "remove", "skill_id": "skill-a", "expected_version": "1"},
            {"action": "set_pool", "series_id": "column-a", "expected_version": 1,
             "skill_ids": [], "defaults": {"image": [], "video": []}},
            {"action": "set_pool", "series_id": "column-a", "expected_version": 1, "skill_ids": None},
            {"action": "unknown"},
            {},
            [],
        ]
        for arguments in invalid:
            with self.subTest(arguments=arguments), self.assertRaises(ValidationError):
                self.tool.parse_arguments(json.dumps(arguments))

    def test_recursive_schema_hides_account_without_mutating_validation(self):
        original = [self.tool.to_schema()]
        snapshot = deepcopy(original)
        view = model_tool_schemas(original, self.context)
        self.assertNotIn('"creator_id"', json.dumps(view))
        self.assertEqual(original, snapshot)
        raw = bind_account_arguments(self.tool.name, '{"action":"get"}', self.context)
        self.assertEqual(self.tool.parse_arguments(raw)["creator_id"], "account-a")
        for owner in ("account-b", None):
            with self.subTest(owner=owner), self.assertRaises(AccountArgumentRejected):
                bind_account_arguments(self.tool.name, json.dumps({"action": "get", "creator_id": owner}),
                                       self.context)
        result = execute_tool_call(ToolCall("scope", self.tool.name,
            '{"action":"get","creator_id":"account-b","unknown":null}'),
            self.context, model_requested=True)
        self.assertEqual(result.error_type, "agent_scope_rejected")

    def test_preserve_defaults_keeps_valid_family_and_does_not_mutate_history(self):
        column = {"defaults": {"image": [{"skill_id": "old", "role": "primary"}], "video": []},
                  "items": [{"id": "old", "family_id": "family"}]}
        account = {"available_versions": [{"id": "candidate", "family_id": "family"}]}
        before = deepcopy((column, account))
        self.assertEqual(_preserve_defaults(column, account, ["candidate"]), column["defaults"])
        self.assertEqual(_preserve_defaults(column, account, []), {"image": [], "video": []})
        self.assertEqual((column, account), before)


if __name__ == "__main__":
    unittest.main()
