"""Model-visible RSI guidance and host-side action validation agree in meaning.

JSON Schema remains provider-compatible and intentionally does not encode
action-dependent requirements; the host continues to enforce them.
"""

import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator
from pydantic import ValidationError

from creatoros.context import RuntimeContext
from creatoros.tools import tool_registry, tools
from creatoros.tools.host_contract import model_tool_schemas
from creatoros.web.chat import ACCOUNT_TOOLS, STUDIO_TOOLS


class RsiRevisionContractTests(unittest.TestCase):
    def _model_tool(self, allowed_tools):
        context = RuntimeContext(Path.cwd(), creator_id="account", allowed_tools=allowed_tools)
        schemas = model_tool_schemas(tools, context)
        return next(item["function"] for item in schemas if item["function"]["name"] == "manage_skill_iteration")

    def test_account_and_studio_see_action_specific_revision_guidance(self):
        for allowed_tools in (ACCOUNT_TOOLS, STUDIO_TOOLS):
            with self.subTest(view=allowed_tools is ACCOUNT_TOOLS):
                function = self._model_tool(allowed_tools)
                action = function["parameters"]["properties"]["action"]["description"]
                revision = function["parameters"]["properties"]["expected_revision"]["description"]
                for text in (action, revision, function["description"]):
                    for term in ("start", "get", "pause", "resume", "continue", "expected_revision"):
                        self.assertIn(term, text)
                    self.assertIn("不传", text)
                    self.assertIn("必", text)

    def test_schema_accepts_old_bad_combination_but_host_rejects_it(self):
        schema = self._model_tool(ACCOUNT_TOOLS)["parameters"]
        Draft202012Validator.check_schema(schema)
        payload = {"action": "start", "series_id": "series", "skill_id": "skill",
                   "topic_id": "topic", "expected_revision": 1}
        # Descriptions guide the model; a cross-field Pydantic validator is not
        # expressible in this unchanged provider-facing object schema.
        self.assertTrue(Draft202012Validator(schema).is_valid(payload))
        with self.assertRaisesRegex(ValidationError, "启动不传任务revision"):
            tool_registry["manage_skill_iteration"].parse_arguments(json.dumps(payload))
        for unsupported in ("if", "then", "else", "oneOf"):
            self.assertNotIn(unsupported, schema)

    def test_host_revision_rules_for_each_action(self):
        iteration_id = "a" * 64
        accepted = [
            {"action": "start", "series_id": "s", "skill_id": "k", "topic_id": "t"},
            {"action": "get", "iteration_id": iteration_id},
            *({"action": action, "iteration_id": iteration_id, "expected_revision": 3}
              for action in ("pause", "resume", "continue")),
        ]
        rejected = [
            {"action": "start", "series_id": "s", "skill_id": "k", "topic_id": "t", "expected_revision": 1},
            {"action": "get", "iteration_id": iteration_id, "expected_revision": 1},
            *({"action": action, "iteration_id": iteration_id}
              for action in ("pause", "resume", "continue")),
        ]
        tool = tool_registry["manage_skill_iteration"]
        for payload in accepted:
            with self.subTest(accepted=payload):
                self.assertEqual(tool.parse_arguments(json.dumps(payload))["action"], payload["action"])
        for payload in rejected:
            with self.subTest(rejected=payload), self.assertRaises(ValidationError):
                tool.parse_arguments(json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
