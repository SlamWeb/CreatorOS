"""Offline validation for the optional schema cleanup experiment."""
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator
from creatoros.context import RuntimeContext
from creatoros.tools import tools
from creatoros.tools.host_contract import model_tool_schemas
from creatoros.web.chat import ACCOUNT_TOOLS
from tests.live_schema_trim_comparison import trim


class SchemaTrimValidation(unittest.TestCase):
    def test_schema_and_business_annotations_preserved(self):
        raw = {"type": "function", "function": {"name": "f", "description": "保留业务负向规则",
            "parameters": {"type": "object", "properties": {
                "field": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None,
                          "description": "不可修改已有记录"},
                "opt": {"type": "object", "default": {"default": None, "title": "do not alter"}},
            }, "$defs": {"Thing": {"title": "Thing", "type": "object"}}}}}
        candidate, counts = trim([raw])
        schema = candidate[0]["function"]["parameters"]
        self.assertEqual(counts, {"null_defaults": 1, "definition_titles": 1, "nullable_anyof": 1})
        self.assertEqual(candidate[0]["function"]["description"], "保留业务负向规则")
        self.assertEqual(schema["properties"]["field"]["description"], "不可修改已有记录")
        self.assertEqual(schema["properties"]["opt"]["default"], {"default": None, "title": "do not alter"})
        before = Draft202012Validator(raw["function"]["parameters"])
        after = Draft202012Validator(schema)
        for value in [{}, {"field": None}, {"field": "x"}, {"field": 1}, {"field": []},
                      {"opt": None}, {"opt": {"x": 1}}]:
            self.assertEqual(before.is_valid(value), after.is_valid(value), value)

    def test_real_registry_constraints_and_names_remain_valid(self):
        scope = RuntimeContext(Path.cwd(), creator_id="scope", archive_only_reads=True, allowed_tools=ACCOUNT_TOOLS)
        original = model_tool_schemas(tools, scope)
        reduced, _ = trim(original)
        self.assertEqual([v["function"]["name"] for v in original], [v["function"]["name"] for v in reduced])
        for before, after in zip(original, reduced):
            self.assertEqual(before["function"]["description"], after["function"]["description"])
            Draft202012Validator.check_schema(after["function"]["parameters"])


if __name__ == "__main__":
    unittest.main()
