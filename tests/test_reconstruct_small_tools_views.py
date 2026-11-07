"""Focused model-view and scope checks for the final small tools."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from creatoros.ai.context import estimate_tokens
from creatoros.context import RuntimeContext
from creatoros.tools import tool_registry, tools
from creatoros.tools.discovery_gateway import ToolGateway
from creatoros.tools.host_contract import model_tool_schemas
from creatoros.tools.action_catalog import build_action_catalog
from creatoros.tools.model_action_views import apply_model_action_views


def views(name, root, creator_id='owner'):
    ctx = RuntimeContext(root, creator_id=creator_id, session_file=root/'messages.json',
                         archive_only_reads=True, allowed_tools=frozenset({name}))
    schemas = model_tool_schemas(tools, ctx)
    original = deepcopy(schemas)
    old = ToolGateway(ctx, schemas, model_view_version='v1').catalog[name]['schema']
    new = ToolGateway(ctx, schemas, model_view_version='v2').catalog[name]['schema']
    canonical = ToolGateway(ctx, schemas, model_view_version='v2', entry_mode='canonical').model_schemas()[0]
    return schemas, original, old, new, canonical


class SmallToolViewTests(unittest.TestCase):
    def test_list_creators_keeps_pagination_and_original_parser(self):
        with TemporaryDirectory(prefix='list-creators-view-') as tmp:
            schemas, original, old, new, canonical = views('list_creators', Path(tmp))
            self.assertEqual(schemas, original)
            self.assertEqual(new, canonical)
            self.assertLess(estimate_tokens(new), estimate_tokens(old))
            params = new['function']['parameters']
            self.assertEqual(set(params['properties']), {'offset', 'limit'})
            self.assertFalse(params.get('required'))
            self.assertNotIn('additionalProperties', params)
            self.assertNotIn('title', params)
            parser = tool_registry['list_creators'].parse_arguments
            self.assertEqual(parser('{}'), {'offset': 0, 'limit': 20})
            self.assertEqual(parser('{"offset":20,"limit":30}'), {'offset':20,'limit':30})
            with self.assertRaises(ValueError):
                parser('{"limit":101}')

    def test_list_creator_series_is_scoped_only_when_account_bound(self):
        with TemporaryDirectory(prefix='list-series-view-') as tmp:
            root = Path(tmp)
            schemas, original, old, new, canonical = views('list_creator_series', root)
            self.assertEqual(schemas, original)
            self.assertEqual(new, canonical)
            self.assertLess(estimate_tokens(new), estimate_tokens(old))
            self.assertEqual(new['function']['parameters']['properties'], {})
            self.assertNotIn('title', new['function']['parameters'])
            overview = RuntimeContext(root, creator_id=None, allowed_tools=frozenset({'list_creator_series'}))
            overview_schema = model_tool_schemas(tools, overview)
            catalog = build_action_catalog(overview_schema)
            overview_old = apply_model_action_views(catalog, 'v1')['list_creator_series']['schema']
            overview_new = apply_model_action_views(catalog, 'v2')['list_creator_series']['schema']
            self.assertEqual(overview_new, overview_old)
            overview_params = overview_new['function']['parameters']
            self.assertIn('creator_id', overview_params['properties'])
            self.assertIn('creator_id', overview_params['required'])

    def test_cancel_skill_extraction_uses_only_requested_job(self):
        with TemporaryDirectory(prefix='cancel-extraction-view-') as tmp:
            schemas, original, old, new, canonical = views('cancel_skill_extraction', Path(tmp))
            self.assertEqual(schemas, original)
            self.assertEqual(new, canonical)
            self.assertLess(estimate_tokens(new), estimate_tokens(old))
            params = new['function']['parameters']
            self.assertEqual(set(params['properties']), {'job_id'})
            self.assertEqual(params['required'], ['job_id'])
            self.assertIn('不等于', new['function']['description'])
            parser = tool_registry['cancel_skill_extraction'].parse_arguments
            self.assertEqual(parser('{"job_id":"job-a"}'), {'job_id':'job-a'})
            with self.assertRaises(ValueError):
                parser('{}')


if __name__ == '__main__':
    unittest.main()
