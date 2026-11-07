"""The batch lookup model view preserves the original read contract."""
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
from creatoros.tools.studio import ResearchBatchArgs


class ResearchBatchViewTests(unittest.TestCase):
    def test_compact_single_batch_view_preserves_original_parser(self):
        with TemporaryDirectory(prefix='research-batch-view-') as directory:
            root = Path(directory)
            ctx = RuntimeContext(root, creator_id='owner', session_file=root / 'session.json',
                                 archive_only_reads=True, allowed_tools=frozenset({'get_topic_research'}))
            scoped = model_tool_schemas(tools, ctx)
            original = deepcopy(scoped)
            v1 = ToolGateway(ctx, scoped, model_view_version='v1').catalog['get_topic_research']['schema']
            v2 = ToolGateway(ctx, scoped, model_view_version='v2', entry_mode='canonical').model_schemas()
            self.assertEqual(scoped, original)
            self.assertEqual(v1, scoped[0])
            self.assertEqual([item['function']['name'] for item in v2], ['get_topic_research'])
            self.assertLess(estimate_tokens(v2[0]), estimate_tokens(v1))
            function = v2[0]['function']
            self.assertEqual(function['parameters']['required'], ['batch_id'])
            self.assertEqual(set(function['parameters']['properties']), {'batch_id'})
            self.assertEqual(function['parameters']['properties']['batch_id']['type'], 'string')
            for term in ('ready', 'stale', 'queued', 'requested_count', 'returned_count', 'status', 'unknown', 'url'):
                self.assertIn(term, function['description'])
            self.assertEqual(ResearchBatchArgs.model_validate({'batch_id': 'a' * 32}).batch_id, 'a' * 32)
            for invalid in ({}, {'batch_id': 'invented'}, {'batch_id': 'a' * 32, 'offset': 1}):
                with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                    ResearchBatchArgs.model_validate(invalid)


if __name__ == '__main__':
    unittest.main()
