"""Provider schemas omit only redundant property titles, never capabilities."""
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

from creatoros.ai.context import estimate_tokens
from creatoros.context import RuntimeContext
from creatoros.tools import tools
from creatoros.tools.host_contract import model_tool_schemas
from creatoros.web.chat import ACCOUNT_TOOLS


class ToolSchemaContextTest(unittest.TestCase):
    def setUp(self):
        self.context = RuntimeContext(Path.cwd(), creator_id="scope-a", archive_only_reads=True,
                                      allowed_tools=ACCOUNT_TOOLS)

    def test_only_duplicated_property_title_annotations_are_removed(self):
        source = deepcopy(tools)
        with patch("creatoros.tools.host_contract._strip_redundant_property_titles"):
            before = model_tool_schemas(tools, self.context)
        after = model_tool_schemas(tools, self.context)
        self.assertEqual(tools, source)
        self.assertEqual([x['function']['name'] for x in before], [x['function']['name'] for x in after])
        removed = []

        def compare(old, new, parent=None):
            if isinstance(old, dict):
                self.assertIsInstance(new, dict)
                for key in old.keys() - new.keys():
                    self.assertEqual(key, "title")
                    self.assertIsInstance(parent, str)
                    self.assertEqual(old[key], parent.replace("_", " ").title())
                    removed.append((parent, old[key]))
                self.assertFalse(new.keys() - old.keys())
                for key in old.keys() & new.keys():
                    if key == 'properties' and isinstance(old[key], dict):
                        self.assertEqual(old[key].keys(), new[key].keys())
                        for name in old[key]:
                            compare(old[key][name], new[key][name], parent=name)
                    else:
                        compare(old[key], new[key])
            elif isinstance(old, list):
                self.assertEqual(len(old), len(new))
                for left, right in zip(old, new):
                    compare(left, right)
            else:
                self.assertEqual(old, new)

        compare(before, after)
        self.assertGreater(len(removed), 0)
        self.assertGreater(estimate_tokens(before), estimate_tokens(after))
        for tool in after:
            Draft202012Validator.check_schema(tool['function']['parameters'])

    def test_real_title_property_custom_titles_and_data_are_preserved(self):
        from creatoros.tools.host_contract import _strip_redundant_property_titles
        schema = {
            'type': 'object', 'required': ['title'],
            'properties': {'title': {'title': 'Title', 'type': 'string'},
                           'resource_id': {'title': 'Important identifier', 'type': 'string'},
                           'item_name': {'title': 'Item Name', 'type': 'string'}},
            'default': {'title': 'keep this actual data'},
            'examples': [{'title': 'keep this example'}],
            '$defs': {'Choice': {'type': 'object', 'properties': {
                'enum_value': {'title': 'Enum Value', 'const': {'title': 'actual data'}}}}},
        }
        expected = deepcopy(schema)
        _strip_redundant_property_titles(schema)
        self.assertIn('title', schema['properties'])
        self.assertEqual(schema['required'], expected['required'])
        self.assertNotIn('title', schema['properties']['title'])
        self.assertEqual(schema['properties']['resource_id']['title'], 'Important identifier')
        self.assertNotIn('title', schema['properties']['item_name'])
        self.assertEqual(schema['default'], expected['default'])
        self.assertEqual(schema['examples'], expected['examples'])
        self.assertEqual(schema['$defs']['Choice']['properties']['enum_value']['const'], {'title': 'actual data'})
        Draft202012Validator.check_schema(schema)
        for candidate in ({'title': 'ok'}, {'title': ''}, {'resource_id': 'x'}):
            self.assertEqual(Draft202012Validator(expected).is_valid(candidate),
                             Draft202012Validator(schema).is_valid(candidate))


if __name__ == '__main__':
    unittest.main()
