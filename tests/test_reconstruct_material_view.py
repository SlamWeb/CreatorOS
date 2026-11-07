"""Compact material schema preserves required business inputs and parser authority."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pydantic import ValidationError
from creatoros.ai.context import estimate_tokens
from creatoros.context import RuntimeContext
from creatoros.tools import tools
from creatoros.tools.canonical_model_views import canonical_v2_schemas
from creatoros.tools.discovery_gateway import ToolGateway
from creatoros.tools.host_contract import model_tool_schemas
from creatoros.tools.staged_production import ManageMaterialArgs


class MaterialViewTests(unittest.TestCase):
    def test_new_action_falls_back_to_scoped_schema(self):
        source = {'type': 'function', 'function': {'name': 'manage_material', 'parameters': {
            'type': 'object', 'properties': {'action': {'type': 'string', 'enum': ['list', 'future']}}}}}
        catalog = {'manage_material.list': {'name': 'manage_material', 'action': 'list'},
                   'manage_material.future': {'name': 'manage_material', 'action': 'future'}}
        result = canonical_v2_schemas([source], catalog)
        self.assertEqual(result, [source])
        self.assertIsNot(result[0], source)

    def test_schema_is_compact_and_groups_requirements(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            context = RuntimeContext(root, creator_id='account', session_file=root / 'session.json',
                                     archive_only_reads=True, allowed_tools=frozenset({'manage_material'}))
            scoped = model_tool_schemas(tools, context)
            before = deepcopy(scoped)
            view = ToolGateway(context, scoped, model_view_version='v2', entry_mode='canonical').model_schemas()[0]
            self.assertEqual(scoped, before)
            self.assertLess(estimate_tokens(view), estimate_tokens(scoped[0]))
            params = view['function']['parameters']
            self.assertEqual(set(params['properties']), {'action', 'material_id', 'source_url', 'import_id',
                'start', 'duration', 'force', 'offset', 'limit'})
            expected = {'list': set(), 'get': {'material_id'}, 'analyze': {'material_id'},
                        'analysis': {'material_id'}, 'analysis_status': {'material_id'},
                        'import_link': {'source_url'}, 'import_status': {'import_id'}}
            self.assertEqual(set(params['properties']['action']['enum']), set(expected))
            for action, required in expected.items():
                branches = [x for x in params['oneOf'] if action in x['properties']['action']['enum']]
                self.assertEqual(len(branches), 1, action)
                self.assertEqual(set(branches[0].get('required', [])), required)
            self.assertEqual(len(params['oneOf']), 4)

    def test_original_parser_keeps_read_import_and_analysis_rules(self):
        material_id = 'a' * 64
        share = '音乐分享文案 https://example.test/path'
        parsed = ManageMaterialArgs.model_validate({'action': 'import_link', 'source_url': share})
        self.assertEqual(parsed.source_url, share)
        parsed = ManageMaterialArgs.model_validate({'action': 'analyze', 'material_id': material_id,
                                                      'start': 12.5, 'duration': 18, 'force': True})
        self.assertEqual((parsed.start, parsed.duration, parsed.force), (12.5, 18, True))
        for data in ({'action': 'get'}, {'action': 'import_link'}, {'action': 'import_status'},
                     {'action': 'analyze', 'material_id': material_id, 'source_url': share},
                     {'action': 'list', 'import_id': 'abc'}):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                ManageMaterialArgs.model_validate(data)


if __name__ == '__main__':
    unittest.main()
