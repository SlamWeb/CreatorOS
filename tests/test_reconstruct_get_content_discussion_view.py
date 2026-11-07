"""Discussion reads distinguish stored history from active-task observation."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from creatoros.ai.context import estimate_tokens
from creatoros.context import RuntimeContext
from creatoros.tools import tools
from creatoros.tools.content_discussion import GetContentDiscussionArgs, get_content_discussion
from creatoros.tools.discovery_gateway import ToolGateway
from creatoros.tools.host_contract import model_tool_schemas


class ContentDiscussionViewTests(unittest.TestCase):
    def test_view_and_active_or_completed_read(self):
        with TemporaryDirectory(prefix='discussion-read-view-') as directory:
            root = Path(directory)
            ctx = RuntimeContext(root, creator_id='owner', session_file=root / 'session.json',
                                 archive_only_reads=True, allowed_tools=frozenset({'get_content_discussion'}))
            scoped = model_tool_schemas(tools, ctx)
            original = deepcopy(scoped)
            v1 = ToolGateway(ctx, scoped, model_view_version='v1').catalog['get_content_discussion']['schema']
            v2 = ToolGateway(ctx, scoped, model_view_version='v2', entry_mode='canonical').model_schemas()
            self.assertEqual(scoped, original)
            self.assertEqual(v1, scoped[0])
            self.assertEqual([item['function']['name'] for item in v2], ['get_content_discussion'])
            self.assertLess(estimate_tokens(v2[0]), estimate_tokens(v1))
            params = v2[0]['function']['parameters']
            self.assertEqual(set(params['properties']), {'run_id'})
            self.assertEqual(params['required'], ['run_id'])
            self.assertEqual(GetContentDiscussionArgs(run_id='run').run_id, 'run')

        completed = {'id': 'old', 'status': 'completed', 'reply': '旧记录'}
        active = {'id': 'new', 'status': 'running', 'run_id': 'run'}
        calls = []
        class Client:
            base_url = 'http://studio.test'
            items = [completed, active]
            def __init__(self, *args, **kwargs): pass
            def close(self): pass
            def request(self, method, path, **kwargs):
                calls.append((method, path))
                return {'items': self.items}
        context = SimpleNamespace(studio_url='http://studio.test', agent_session_id='owner')
        with patch('creatoros.tools.studio.StudioClient', Client):
            result = json.loads(get_content_discussion('run', context=context).content)
            self.assertEqual(result['id'], 'new')
            self.assertNotIn('items', result)
            self.assertEqual(calls, [('GET', '/api/runs/run/discussion')])
            calls.clear()
            Client.items = [completed]
            result = json.loads(get_content_discussion('run', context=context).content)
            self.assertEqual(result['items'], [completed])
            self.assertEqual(calls, [('GET', '/api/runs/run/discussion')])


if __name__ == '__main__':
    unittest.main()
