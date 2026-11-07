"""Publication preparation view and draft-only dispatch without remote publishing."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from creatoros.ai.context import estimate_tokens
from creatoros.context import RuntimeContext
from creatoros.tools import tools
from creatoros.tools.discovery_gateway import ToolGateway
from creatoros.tools.host_contract import model_tool_schemas
from creatoros.tools.publication import PreparePublicationArgs, prepare_publication


class PreparePublicationViewTests(unittest.TestCase):
    def test_compact_model_view_retains_business_inputs(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            context = RuntimeContext(root, creator_id='owner', session_file=root/'session.json',
                                     archive_only_reads=True, allowed_tools=frozenset({'prepare_publication'}))
            scoped = model_tool_schemas(tools, context)
            before = deepcopy(scoped)
            view = ToolGateway(context, scoped, model_view_version='v2', entry_mode='canonical').model_schemas()[0]
            self.assertEqual(scoped, before)
            self.assertLess(estimate_tokens(view), estimate_tokens(scoped[0]))
            function = view['function']
            params = function['parameters']
            self.assertEqual(set(params['properties']), {'run_id', 'title', 'body', 'tags', 'visibility'})
            self.assertEqual(params['required'], ['run_id'])
            self.assertEqual(params['properties']['visibility']['enum'], ['公开可见', '仅自己可见', '仅互关好友可见'])
            self.assertIn('空', params['properties']['tags']['description'])
            self.assertIn('公开', params['properties']['visibility']['description'])

    def test_empty_tags_and_default_visibility_dispatch_only_draft(self):
        class Client:
            def __init__(self): self.calls = []
            def request(self, method, path, payload, timeout_seconds):
                self.calls.append((method, path, payload, timeout_seconds))
                return {'id': 'p1', 'run_id': 'r1', 'status': 'draft', 'account': 'owner',
                        'payload': {'title': '标题'}}
        parsed = PreparePublicationArgs.model_validate({'run_id': 'r1', 'tags': []})
        self.assertEqual(parsed.visibility, '公开可见')
        client = Client()
        with patch('creatoros.tools.publication._call', side_effect=lambda fn, context: fn(client)):
            result = prepare_publication(**parsed.model_dump())
        self.assertEqual(result['status'], 'draft')
        self.assertEqual(len(client.calls), 1)
        method, path, payload, _ = client.calls[0]
        self.assertEqual((method, path), ('POST', '/api/runs/r1/publication-request'))
        self.assertEqual(payload['tags'], [])
        self.assertEqual(payload['visibility'], '公开可见')


if __name__ == '__main__':
    unittest.main()
