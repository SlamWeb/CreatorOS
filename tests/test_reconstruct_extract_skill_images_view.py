"""Focused model-view checks for extracting Skills from chat images."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from creatoros.context import RuntimeContext
from creatoros.tools import tool_registry, tools
from creatoros.tools.discovery_gateway import ToolGateway
from creatoros.tools.host_contract import model_tool_schemas

NAME = 'extract_skill_from_images'


class ExtractSkillImagesViewTests(unittest.TestCase):
    def test_model_view_retains_modes_and_attachment_contract(self):
        with TemporaryDirectory(prefix='extract-images-view-') as directory:
            root = Path(directory)
            ctx = RuntimeContext(root, creator_id='owner', session_file=root/'messages.json',
                                 archive_only_reads=True, allowed_tools=frozenset({NAME}))
            schemas = model_tool_schemas(tools, ctx)
            original = deepcopy(schemas)
            old = ToolGateway(ctx, schemas, model_view_version='v1')
            v2 = ToolGateway(ctx, schemas, model_view_version='v2')
            canonical = ToolGateway(ctx, schemas, model_view_version='v2', entry_mode='canonical')
            before, after = old.catalog[NAME]['schema'], v2.catalog[NAME]['schema']
            self.assertEqual(schemas, original)
            self.assertEqual(canonical.model_schemas(), [after])
            self.assertNotEqual(after, before)
            params = after['function']['parameters']
            self.assertEqual(set(params['properties']), {'attachment_ids', 'mode', 'instruction'})
            self.assertEqual(params['required'], ['attachment_ids'])
            self.assertEqual(params['properties']['mode']['enum'], ['single', 'mind', 'visual', 'pair'])
            description = params['properties']['mode']['description']
            for word in ('默认', 'single', 'mind', 'visual', 'pair'):
                self.assertIn(word, description)
            for key in ('title', 'additionalProperties', 'minItems', 'maxItems', 'maxLength', 'default'):
                self.assertNotIn('"' + key + '"', json.dumps(params))
            parser = tool_registry[NAME].parse_arguments
            self.assertEqual(parser(json.dumps({'attachment_ids':['a']}))['mode'], 'single')
            parsed = parser(json.dumps({'attachment_ids':['a','b'], 'mode':'pair', 'instruction':'保留构图'}, ensure_ascii=False))
            self.assertEqual((parsed['attachment_ids'], parsed['mode'], parsed['instruction']),
                             (['a','b'], 'pair', '保留构图'))
            with self.assertRaises(ValueError):
                parser(json.dumps({'attachment_ids':[], 'mode':'single'}))


if __name__ == '__main__':
    unittest.main()
