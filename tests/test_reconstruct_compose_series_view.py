"""Model-facing create-series view; business parser remains authoritative."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from creatoros.ai.context import estimate_tokens
from creatoros.context import RuntimeContext
from creatoros.tools import tools
from creatoros.tools.discovery_gateway import ToolGateway
from creatoros.tools.host_contract import model_tool_schemas


class ComposeSeriesViewTests(unittest.TestCase):
    def test_compact_scoped_view_preserves_business_fields(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            context = RuntimeContext(root, creator_id='owner', session_file=root / 'session.json',
                                     archive_only_reads=True, allowed_tools=frozenset({'compose_series'}))
            scoped = model_tool_schemas(tools, context)
            original = deepcopy(scoped)
            old = ToolGateway(context, scoped, model_view_version='v1').catalog['compose_series']['schema']
            view = ToolGateway(context, scoped, model_view_version='v2', entry_mode='canonical').model_schemas()[0]
            self.assertEqual(scoped, original)
            self.assertEqual(old, original[0])
            self.assertLess(estimate_tokens(view), estimate_tokens(old))
            function = view['function']
            self.assertEqual(function['name'], 'compose_series')
            params = function['parameters']
            self.assertEqual(set(params['properties']), {'name', 'description', 'audience', 'skill_name',
                                                         'mind_skill_id', 'production_skill_id'})
            self.assertEqual(params['required'], ['name'])
            self.assertTrue(all(x['type'] == 'string' for x in params['properties'].values()))
            descriptions = str(function)
            self.assertIn('二选一', descriptions)
            self.assertIn('local_path', descriptions)


if __name__ == '__main__':
    unittest.main()
