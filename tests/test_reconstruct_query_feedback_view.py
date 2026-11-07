"""Feedback view keeps account scoping and pagination inputs."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from creatoros.agent.tool_protocol import select_tool_protocol
from creatoros.ai.context import estimate_tokens
from creatoros.context import RuntimeContext
from creatoros.tools import tools
from creatoros.tools.discovery_gateway import ToolGateway
from creatoros.tools.host_contract import model_tool_schemas


class FeedbackViewTests(unittest.TestCase):
    def test_account_compact_view_and_overview_contract(self):
        with TemporaryDirectory(prefix='feedback-view-') as directory:
            root = Path(directory)
            account = RuntimeContext(root, creator_id='owner', session_file=root / 'session.json',
                                     archive_only_reads=True, allowed_tools=frozenset({'query_feedback'}))
            scoped = model_tool_schemas(tools, account)
            original = deepcopy(scoped)
            v1 = ToolGateway(account, scoped, model_view_version='v1').catalog['query_feedback']['schema']
            v2 = ToolGateway(account, scoped, model_view_version='v2', entry_mode='canonical').model_schemas()
            self.assertEqual(scoped, original)
            self.assertEqual(v1, scoped[0])
            self.assertEqual([item['function']['name'] for item in v2], ['query_feedback'])
            self.assertLess(estimate_tokens(v2[0]), estimate_tokens(v1))
            params = v2[0]['function']['parameters']
            self.assertEqual(set(params['properties']), {'metric', 'offset', 'limit'})
            self.assertFalse(params.get('required'))
            self.assertEqual(set(params['properties']['metric']['enum']),
                             {'favorites', 'likes', 'comments', 'views', 'shares'})
            overview = RuntimeContext(root, creator_id=None, session_file=root / 'overview.json',
                                      archive_only_reads=True, allowed_tools=frozenset({'query_feedback'}))
            self.assertEqual(select_tool_protocol([], overview), 'legacy')
            old = model_tool_schemas(tools, overview)[0]['function']['parameters']
            self.assertIn('creator_id', old['properties'])
            self.assertIn('creator_id', old['required'])


if __name__ == '__main__':
    unittest.main()
