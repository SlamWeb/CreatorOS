"""Compact read view preserves the Run identifier and version semantics."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pydantic import ValidationError

from creatoros.ai.context import estimate_tokens
from creatoros.context import RuntimeContext
from creatoros.tools import tools
from creatoros.tools.discovery_gateway import ToolGateway
from creatoros.tools.host_contract import model_tool_schemas
from creatoros.tools.studio import GetRunArgs


class ContentRunViewTests(unittest.TestCase):
    def test_view_and_original_parser(self):
        with TemporaryDirectory(prefix='content-run-view-') as directory:
            root = Path(directory)
            ctx = RuntimeContext(root, creator_id='owner', session_file=root / 'session.json',
                                 archive_only_reads=True, allowed_tools=frozenset({'get_content_run'}))
            scoped = model_tool_schemas(tools, ctx)
            original = deepcopy(scoped)
            v1 = ToolGateway(ctx, scoped, model_view_version='v1').catalog['get_content_run']['schema']
            v2 = ToolGateway(ctx, scoped, model_view_version='v2', entry_mode='canonical').model_schemas()
            self.assertEqual(scoped, original)
            self.assertEqual(v1, scoped[0])
            self.assertEqual([item['function']['name'] for item in v2], ['get_content_run'])
            self.assertLess(estimate_tokens(v2[0]), estimate_tokens(v1))
            function = v2[0]['function']
            self.assertEqual(set(function['parameters']['properties']), {'run_id'})
            self.assertEqual(function['parameters']['required'], ['run_id'])
            for term in ('revision_id', 'artifact_digest', 'awaiting_approval', 'approved'):
                self.assertIn(term, function['description'])
            self.assertEqual(GetRunArgs(run_id='run-a').run_id, 'run-a')
            with self.assertRaises(ValidationError):
                GetRunArgs(run_id='')


if __name__ == '__main__':
    unittest.main()
