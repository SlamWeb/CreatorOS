"""Contract checks for the request_content_revision model view."""
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

NAME = 'request_content_revision'


class ContentRevisionViewTests(unittest.TestCase):
    def test_compact_view_preserves_required_arguments_and_v1(self):
        with TemporaryDirectory(prefix='revision-view-') as directory:
            root = Path(directory)
            context = RuntimeContext(root, creator_id='owner', session_file=root/'messages.json',
                                     archive_only_reads=True, allowed_tools=frozenset({NAME}))
            schemas = model_tool_schemas(tools, context)
            original = deepcopy(schemas)
            old = ToolGateway(context, schemas, model_view_version='v1')
            new = ToolGateway(context, schemas, model_view_version='v2')
            canonical = ToolGateway(context, schemas, model_view_version='v2', entry_mode='canonical')
            before = old.catalog[NAME]['schema']
            after = new.catalog[NAME]['schema']
            self.assertEqual(schemas, original)
            self.assertEqual(canonical.model_schemas(), [after])
            self.assertLess(estimate_tokens(after), estimate_tokens(before))
            params = after['function']['parameters']
            self.assertEqual(set(params['required']), {'run_id', 'instruction', 'expected_version'})
            self.assertEqual(set(params['properties']), set(params['required']))
            self.assertEqual(params['properties']['expected_version']['type'], 'integer')
            for machine_field in ('title','additionalProperties','minLength','maxLength','exclusiveMinimum'):
                self.assertNotIn('"' + machine_field + '"', json.dumps(params))
            parsed = tool_registry[NAME].parse_arguments(json.dumps({
                'run_id':'run-test', 'instruction':'保留主体，仅改标题', 'expected_version':7}, ensure_ascii=False))
            self.assertEqual(parsed, {'run_id':'run-test', 'instruction':'保留主体，仅改标题', 'expected_version':7})
            with self.assertRaises(ValueError):
                tool_registry[NAME].parse_arguments(json.dumps({'run_id':'run-test','instruction':'返工'}))


if __name__ == '__main__':
    unittest.main()
