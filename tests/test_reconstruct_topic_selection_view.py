"""Direct model-view checks for the topic selection preview contract."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from creatoros.ai.context import estimate_tokens
from creatoros.context import RuntimeContext
from creatoros.tools import tool_registry, tools
from creatoros.tools.discovery_gateway import ToolGateway
from creatoros.tools.host_contract import model_tool_schemas

NAME = 'prepare_topic_selection'


class TopicSelectionViewTests(unittest.TestCase):
    def test_canonical_v2_is_compact_and_preserves_selection_contract(self):
        with TemporaryDirectory(prefix='topic-selection-view-') as directory:
            root = Path(directory)
            context = RuntimeContext(root, creator_id='owner', session_file=root/'messages.json',
                                     archive_only_reads=True, allowed_tools=frozenset({NAME}))
            schemas = model_tool_schemas(tools, context)
            original = deepcopy(schemas)
            old = ToolGateway(context, schemas, model_view_version='v1')
            new = ToolGateway(context, schemas, model_view_version='v2')
            direct = ToolGateway(context, schemas, model_view_version='v2', entry_mode='canonical')
            old_schema = old.catalog[NAME]['schema']
            schema = new.catalog[NAME]['schema']
            self.assertEqual(direct.model_schemas(), [schema])
            self.assertEqual(schemas, original)
            self.assertLess(estimate_tokens(schema), estimate_tokens(old_schema))
            params = schema['function']['parameters']
            self.assertEqual(set(params['required']), {'batch_id', 'selections'})
            self.assertEqual(set(params['properties']), {'batch_id', 'selections'})
            item = params['properties']['selections']['items']
            self.assertEqual(set(item['properties']), {'candidate_id', 'title', 'angle'})
            self.assertEqual(item['required'], ['candidate_id'])
            encoded = json.dumps(params, ensure_ascii=False)
            for machine_field in ('$defs', '$ref', 'anyOf', 'additionalProperties', 'pattern',
                                  'minLength', 'maxLength', 'minItems', 'maxItems'):
                self.assertNotIn('"' + machine_field + '"', encoded)

    def test_registered_parser_preserves_order_and_omitted_fields(self):
        parsed = tool_registry[NAME].parse_arguments(json.dumps({
            'batch_id': 'a'*32,
            'selections': [{'candidate_id': 'c2', 'title': '新标题', 'angle': '新角度'},
                           {'candidate_id': 'c1'}],
        }, ensure_ascii=False))
        self.assertEqual([s['candidate_id'] for s in parsed['selections']], ['c2', 'c1'])
        self.assertEqual(parsed['selections'][0]['title'], '新标题')
        self.assertEqual(parsed['selections'][0]['angle'], '新角度')
        self.assertIsNone(parsed['selections'][1]['title'])
        self.assertIsNone(parsed['selections'][1]['angle'])
        with self.assertRaises(ValueError):
            tool_registry[NAME].parse_arguments(json.dumps({'batch_id': 'a'*32, 'selections': []}))


if __name__ == '__main__':
    unittest.main()
