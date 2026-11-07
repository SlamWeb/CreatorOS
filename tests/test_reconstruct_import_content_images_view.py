"""Focused model-view checks for importing finished chat images."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from creatoros.context import RuntimeContext
from creatoros.tools import tool_registry, tools
from creatoros.tools.discovery_gateway import ToolGateway
from creatoros.tools.host_contract import model_tool_schemas

NAME = 'import_content_images'


class ImportContentImagesViewTests(unittest.TestCase):
    def test_model_view_retains_import_and_order_contract(self):
        with TemporaryDirectory(prefix='import-images-view-') as directory:
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
            self.assertEqual(set(params['properties']), {'series_id', 'attachment_ids', 'title', 'body'})
            self.assertEqual(set(params['required']), {'series_id', 'attachment_ids', 'title'})
            self.assertIn('封面', params['properties']['attachment_ids']['description'])
            self.assertIn('成品', after['function']['description'])
            self.assertIn('验收', after['function']['description'])
            for key in ('title', 'additionalProperties', 'minItems', 'maxItems', 'minLength', 'maxLength', 'default'):
                # Field name title is business data, so inspect schema keywords structurally below.
                if key != 'title':
                    self.assertNotIn('"' + key + '"', json.dumps(params))
            self.assertNotIn('title', {k for k in params if k != 'properties'})
            parser = tool_registry[NAME].parse_arguments
            parsed = parser(json.dumps({'series_id':'s', 'attachment_ids':['cover','second'],
                                        'title':'标题', 'body':'正文'}, ensure_ascii=False))
            self.assertEqual(parsed['attachment_ids'], ['cover','second'])
            self.assertEqual(parsed['body'], '正文')
            self.assertEqual(parser(json.dumps({'series_id':'s', 'attachment_ids':['cover'], 'title':'标题'},
                                               ensure_ascii=False))['body'], '')
            with self.assertRaises(ValueError):
                parser(json.dumps({'series_id':'s', 'attachment_ids':[], 'title':'标题'}, ensure_ascii=False))


if __name__ == '__main__':
    unittest.main()
