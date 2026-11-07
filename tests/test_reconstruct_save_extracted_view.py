"""Targeted contract checks for the extraction-save model view."""
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

NAME = 'save_extracted_skills'


class SaveExtractedViewTests(unittest.TestCase):
    def test_compact_v2_keeps_confirmation_and_digest_contract(self):
        with TemporaryDirectory(prefix='save-extracted-view-') as directory:
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
            self.assertEqual(set(params['properties']), {'job_id', 'expected_digest'})
            self.assertEqual(set(params['required']), {'job_id', 'expected_digest'})
            self.assertIn('确认', after['function']['description'])
            self.assertIn('原样', params['properties']['expected_digest']['description'])
            for key in ('title', 'additionalProperties', 'minLength', 'maxLength', 'pattern'):
                self.assertNotIn('"' + key + '"', json.dumps(params))
            parser = tool_registry[NAME].parse_arguments
            self.assertEqual(parser(json.dumps({'job_id':'job-1','expected_digest':'digest-1'})),
                             {'job_id':'job-1','expected_digest':'digest-1'})
            with self.assertRaises(ValueError):
                parser(json.dumps({'job_id':'job-1'}))


if __name__ == '__main__':
    unittest.main()
