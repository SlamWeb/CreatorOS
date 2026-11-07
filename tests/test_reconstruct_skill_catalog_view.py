"""Targeted contract checks for the Skill catalog model view."""
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

NAME = 'list_producer_skills'


class SkillCatalogViewTests(unittest.TestCase):
    def test_compact_v2_preserves_catalog_and_filter_contract(self):
        with TemporaryDirectory(prefix='skill-catalog-view-') as directory:
            root = Path(directory)
            context = RuntimeContext(root, creator_id='owner', session_file=root/'messages.json',
                                     archive_only_reads=True, allowed_tools=frozenset({NAME}))
            schemas = model_tool_schemas(tools, context)
            original = deepcopy(schemas)
            old = ToolGateway(context, schemas, model_view_version='v1')
            new = ToolGateway(context, schemas, model_view_version='v2')
            canonical = ToolGateway(context, schemas, model_view_version='v2', entry_mode='canonical')
            before, after = old.catalog[NAME]['schema'], new.catalog[NAME]['schema']
            self.assertEqual(schemas, original)
            self.assertEqual(canonical.model_schemas(), [after])
            self.assertLess(estimate_tokens(after), estimate_tokens(before))
            params = after['function']['parameters']
            self.assertEqual(set(params['properties']), {'include_versions', 'family_skill_id'})
            self.assertFalse(params.get('required'))
            self.assertEqual(params['properties']['include_versions']['type'], 'boolean')
            self.assertEqual(params['properties']['family_skill_id']['type'], 'string')
            self.assertIn('include_versions=true', params['properties']['family_skill_id']['description'])
            self.assertIn('manage_skill_library', after['function']['description'])
            for key in ('title', 'additionalProperties', 'anyOf', 'pattern', 'default'):
                self.assertNotIn('"' + key + '"', json.dumps(params))
            parser = tool_registry[NAME].parse_arguments
            self.assertFalse(parser('{}')['include_versions'])
            parsed = parser(json.dumps({'include_versions': True, 'family_skill_id': 'sample--0123456789abcdef'}))
            self.assertTrue(parsed['include_versions'])
            self.assertEqual(parsed['family_skill_id'], 'sample--0123456789abcdef')


if __name__ == '__main__':
    unittest.main()
