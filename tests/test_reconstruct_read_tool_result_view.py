"""Keep the session result reader's exact reference and character contract."""
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
from creatoros.tools.models import ReadToolResultArgs


class ReadToolResultViewTests(unittest.TestCase):
    def test_compact_view_and_original_parser(self):
        with TemporaryDirectory(prefix='tool-result-view-') as directory:
            root = Path(directory)
            ctx = RuntimeContext(root, creator_id='owner', session_file=root / 'session.json',
                                 archive_only_reads=True, allowed_tools=frozenset({'read_tool_result'}))
            scoped = model_tool_schemas(tools, ctx)
            original = deepcopy(scoped)
            v1 = ToolGateway(ctx, scoped, model_view_version='v1').catalog['read_tool_result']['schema']
            v2 = ToolGateway(ctx, scoped, model_view_version='v2', entry_mode='canonical').model_schemas()
            self.assertEqual(scoped, original)
            self.assertEqual(v1, scoped[0])
            self.assertEqual([item['function']['name'] for item in v2], ['read_tool_result'])
            self.assertLess(estimate_tokens(v2[0]), estimate_tokens(v1))
            params = v2[0]['function']['parameters']
            self.assertEqual(set(params['properties']), {'result_ref', 'offset', 'limit'})
            self.assertEqual(params['required'], ['result_ref'])
            self.assertEqual(ReadToolResultArgs(result_ref='x').model_dump(),
                             {'result_ref': 'x', 'offset': 1, 'limit': 8000})
            for invalid in ({}, {'result_ref': 'x', 'offset': 0}, {'result_ref': 'x', 'limit': 16001},
                            {'result_ref': 'x', 'search': 'secret'}):
                with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                    ReadToolResultArgs.model_validate(invalid)


if __name__ == '__main__':
    unittest.main()
