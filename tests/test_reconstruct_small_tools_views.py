"""Focused model-view and scope checks for the final small tools."""
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
from creatoros.tools.action_catalog import build_action_catalog
from creatoros.tools.model_action_views import apply_model_action_views
from creatoros.web.chat import ACCOUNT_INSTRUCTIONS, ACCOUNT_TOOLS, STUDIO_TOOLS, WEB_INSTRUCTIONS


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
    def test_new_skill_tools_default_to_one_skill_in_all_model_views(self):
        cases = [
            ('create_skill_draft', {'name': 'sample', 'description': 'sample', 'content': 'prompt'},
             'role', 'legacy_end_to_end', 'mind'),
            ('extract_skill_from_images', {'attachment_ids': ['image']}, 'mode', 'single', 'visual'),
            ('extract_skills_from_artifact', {'request_id': 'request', 'source_text': 'sample'},
             'mode', 'single', 'pair'),
            ('install_producer_skill', {'github_url': 'https://github.com/example/skill'},
             'role', 'legacy_end_to_end', 'production'),
        ]
        for tool_name, arguments, field, default, historical in cases:
            with self.subTest(tool=tool_name):
                tool = tool_registry[tool_name]
                model_schemas = [tool.to_schema()]
                if tool_name in ACCOUNT_TOOLS:
                    with TemporaryDirectory(prefix='new-skill-view-') as tmp:
                        schemas, _, v1, v2, canonical = views(tool_name, Path(tmp))
                    model_schemas.extend((schemas[0], v1, v2, canonical))
                for schema in model_schemas:
                    self.assertNotIn(field, schema['function']['parameters']['properties'])
                self.assertEqual(tool.parse_arguments(json.dumps(arguments))[field], default)
                self.assertEqual(tool.parse_arguments(json.dumps({**arguments, field: historical}))[field], historical)

    def test_single_skill_policy_keeps_existing_scope_and_legacy_editing(self):
        for instructions in (WEB_INSTRUCTIONS, ACCOUNT_INSTRUCTIONS):
            self.assertIn('直接交付一份完整 Skill', instructions)
        self.assertTrue({'install_producer_skill', 'extract_skills_from_artifact'} <= STUDIO_TOOLS)
        self.assertFalse({'install_producer_skill', 'extract_skills_from_artifact'} & ACCOUNT_TOOLS)
        with TemporaryDirectory(prefix='old-skill-view-') as tmp:
            root = Path(tmp)
            _, _, _, compose, _ = views('compose_series', root)
            _, _, _, edit, _ = views('edit_extracted_skills', root)
        self.assertIn('mind_skill_id', compose['function']['parameters']['properties'])
        draft_properties = edit['function']['parameters']['properties']['skills']['items']['properties']
        self.assertIn('mind', draft_properties['role']['enum'])

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
