from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

from creatoros.ai.context import estimate_tokens
from creatoros.context import RuntimeContext
from creatoros.tools import tools
from creatoros.tools.host_contract import _simplify_schema_annotations, model_tool_schemas
from creatoros.web.chat import ACCOUNT_TOOLS
from tests.live_schema_trim_comparison import trim


class SchemaAnnotationCompactionTests(unittest.TestCase):
    def test_preserves_validation_and_does_not_visit_examples_or_defaults(self):
        schema = {"type": "object", "properties": {
            "text": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None,
                     "description": "保留业务规则"},
            "data": {"type": "object", "default": {"default": None, "title": "保留"}},
        }, "$defs": {"Example": {"type": "object", "title": "Example"}},
           "examples": [{"default": None, "title": "保留"}]}
        original = deepcopy(schema)
        _simplify_schema_annotations(schema)
        self.assertEqual(schema["properties"]["text"]["type"], ["string", "null"])
        self.assertNotIn("default", schema["properties"]["text"])
        self.assertEqual(schema["properties"]["text"]["description"], "保留业务规则")
        self.assertEqual(schema["properties"]["data"]["default"], original["properties"]["data"]["default"])
        self.assertEqual(schema["examples"], original["examples"])
        self.assertNotIn("title", schema["$defs"]["Example"])
        for value in ({}, {"text": None}, {"text": "x"}, {"text": 4}, {"data": None}):
            self.assertEqual(Draft202012Validator(original).is_valid(value),
                             Draft202012Validator(schema).is_valid(value))

    def test_account_schema_matches_tested_candidate_and_does_not_change_registry(self):
        scope = RuntimeContext(Path.cwd(), creator_id="scope", archive_only_reads=True, allowed_tools=ACCOUNT_TOOLS)
        source = deepcopy(tools)
        with patch("creatoros.tools.host_contract._simplify_schema_annotations"):
            previous = model_tool_schemas(tools, scope)
        actual = model_tool_schemas(tools, scope)
        expected, _ = trim(previous)
        self.assertEqual(actual, expected)
        self.assertEqual(tools, source)
        self.assertLess(estimate_tokens(actual), estimate_tokens(previous))
        for item in actual:
            Draft202012Validator.check_schema(item["function"]["parameters"])

    def test_other_scopes_keep_prior_schema(self):
        for context in (RuntimeContext(Path.cwd()), RuntimeContext(Path.cwd(), creator_id="scope")):
            with self.subTest(context=context), patch("creatoros.tools.host_contract._simplify_schema_annotations"):
                previous = model_tool_schemas(tools, context)
            self.assertEqual(previous, model_tool_schemas(tools, context))


if __name__ == "__main__":
    unittest.main()
