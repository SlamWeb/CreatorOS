"""Model view and controlled routing for publication lookups."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from creatoros.ai.context import estimate_tokens
from creatoros.context import RuntimeContext
from creatoros.tools import tools
from creatoros.tools.discovery_gateway import ToolGateway
from creatoros.tools.host_contract import model_tool_schemas
from creatoros.tools.publication import GetPublicationArgs, get_publication


class PublicationViewTests(unittest.TestCase):
    def test_compact_view_and_read_reconcile_refresh_routing(self):
        with TemporaryDirectory(prefix='publication-view-') as directory:
            root = Path(directory)
            context = RuntimeContext(root, creator_id='owner', session_file=root / 'session.json',
                                     archive_only_reads=True, allowed_tools=frozenset({'get_publication'}))
            scoped = model_tool_schemas(tools, context)
            original = deepcopy(scoped)
            v1 = ToolGateway(context, scoped, model_view_version='v1').catalog['get_publication']['schema']
            v2 = ToolGateway(context, scoped, model_view_version='v2', entry_mode='canonical').model_schemas()
            self.assertEqual(scoped, original)
            self.assertEqual(v1, scoped[0])
            self.assertEqual([item['function']['name'] for item in v2], ['get_publication'])
            self.assertLess(estimate_tokens(v2[0]), estimate_tokens(v1))
            params = v2[0]['function']['parameters']
            self.assertEqual(set(params['properties']), {'publication_id', 'reconcile', 'refresh_metrics'})
            self.assertEqual(params['required'], ['publication_id'])
            self.assertEqual(GetPublicationArgs(publication_id='pub').model_dump(),
                             {'publication_id': 'pub', 'reconcile': False, 'refresh_metrics': False})

        calls = []
        class Client:
            base_url = 'http://studio.test'
            def __init__(self, *args, **kwargs): pass
            def close(self): pass
            def request(self, method, path, **kwargs):
                calls.append((method, path))
                return {'id': 'pub', 'run_id': 'r', 'status': 'published', 'account': {}, 'payload': {'title': 't'}}

        context = SimpleNamespace(studio_url='http://studio.test', agent_session_id='owner')
        with patch('creatoros.tools.studio.StudioClient', Client):
            for values, expected in [({}, [('GET', '/api/publication-requests/pub')]),
                ({'reconcile': True}, [('POST', '/api/publication-requests/pub/reconcile'), ('GET', '/api/publication-requests/pub')]),
                ({'refresh_metrics': True}, [('POST', '/api/publication-requests/pub/metrics')]),
                ({'reconcile': True, 'refresh_metrics': True}, [('POST', '/api/publication-requests/pub/reconcile'),
                                                                  ('POST', '/api/publication-requests/pub/metrics')])]:
                with self.subTest(values=values):
                    calls.clear()
                    self.assertFalse(get_publication('pub', context=context, **values).is_error)
                    self.assertEqual(calls, expected)


if __name__ == '__main__':
    unittest.main()
