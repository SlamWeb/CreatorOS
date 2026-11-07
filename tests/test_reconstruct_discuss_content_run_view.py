"""Keep version-bound discussion inputs while shortening only its model view."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pydantic import ValidationError

from creatoros.ai.context import estimate_tokens
from creatoros.context import RuntimeContext
from creatoros.tools import tools
from creatoros.tools.content_discussion import DiscussContentRunArgs
from creatoros.tools.discovery_gateway import ToolGateway
from creatoros.tools.host_contract import model_tool_schemas


class DiscussionViewTests(unittest.TestCase):
    def test_view_keeps_all_required_inputs_and_original_parser(self):
        with TemporaryDirectory(prefix='discussion-view-') as directory:
            root = Path(directory)
            context = RuntimeContext(root, creator_id='owner', session_file=root / 'session.json',
                                     archive_only_reads=True, allowed_tools=frozenset({'discuss_content_run'}))
            scoped = model_tool_schemas(tools, context)
            original = deepcopy(scoped)
            v1 = ToolGateway(context, scoped, model_view_version='v1').catalog['discuss_content_run']['schema']
            v2 = ToolGateway(context, scoped, model_view_version='v2', entry_mode='canonical').model_schemas()
            self.assertEqual(scoped, original)
            self.assertEqual(v1, scoped[0])
            self.assertEqual([x['function']['name'] for x in v2], ['discuss_content_run'])
            self.assertLess(estimate_tokens(v2[0]), estimate_tokens(v1))
            params = v2[0]['function']['parameters']
            fields = {'run_id', 'request_id', 'revision_id', 'artifact_digest', 'message'}
            self.assertEqual(set(params['properties']), fields)
            self.assertEqual(set(params['required']), fields)
            self.assertTrue(all(x.get('type') == 'string' for x in params['properties'].values()))
            valid = {'run_id': 'run', 'request_id': 'request-1', 'revision_id': 'rev',
                     'artifact_digest': 'a' * 64, 'message': '请讨论封面'}
            self.assertEqual(DiscussContentRunArgs.model_validate(valid).model_dump(), valid)
            for name in fields:
                with self.subTest(missing=name), self.assertRaises(ValidationError):
                    DiscussContentRunArgs.model_validate({k: v for k, v in valid.items() if k != name})
            with self.assertRaises(ValidationError):
                DiscussContentRunArgs.model_validate({**valid, 'artifact_digest': 'bad'})


if __name__ == '__main__':
    unittest.main()
