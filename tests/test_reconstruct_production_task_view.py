"""Model-facing task control remains compact and action-specific."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from creatoros.ai.context import estimate_tokens
from creatoros.context import RuntimeContext
from creatoros.tools import tools
from creatoros.tools.canonical_model_views import canonical_v2_schemas
from creatoros.tools.discovery_gateway import ToolGateway
from creatoros.tools.host_contract import model_tool_schemas
from creatoros.tools.production_task_control import ManageProductionTaskArgs


class ProductionTaskViewTests(unittest.TestCase):
    def test_schema_keeps_one_tool_and_action_requirements(self):
        with TemporaryDirectory(prefix='task-control-view-') as directory:
            root = Path(directory)
            context = RuntimeContext(root, creator_id='account', session_file=root / 'session.json',
                                     archive_only_reads=True, allowed_tools=frozenset({'manage_production_task'}))
            scoped = model_tool_schemas(tools, context)
            original = deepcopy(scoped)
            views = ToolGateway(context, scoped, model_view_version='v2', entry_mode='canonical').model_schemas()
            self.assertEqual([view['function']['name'] for view in views], ['manage_production_task'])
            self.assertEqual(scoped, original)
            self.assertLess(estimate_tokens(views[0]), estimate_tokens(scoped[0]))
            params = views[0]['function']['parameters']
            self.assertEqual(set(params['properties']), {'action', 'topic_id', 'task_id'})
            self.assertEqual(set(params['required']), {'action', 'topic_id'})
            self.assertEqual(set(params['properties']['action']['enum']), {'list', 'stop', 'remove'})
            for action in ('list', 'stop', 'remove'):
                branches = [b for b in params['oneOf'] if action in b['properties']['action']['enum']]
                self.assertEqual(len(branches), 1)
                self.assertEqual(set(branches[0].get('required', [])), set() if action == 'list' else {'task_id'})
            ManageProductionTaskArgs.model_validate({'action': 'list', 'topic_id': 't'})
            for action in ('stop', 'remove'):
                with self.subTest(action=action), self.assertRaises(ValueError):
                    ManageProductionTaskArgs.model_validate({'action': action, 'topic_id': 't'})
            with self.assertRaises(ValueError):
                ManageProductionTaskArgs.model_validate({'action': 'list', 'topic_id': 't', 'task_id': 'x'})

    def test_new_action_preserves_scoped_schema(self):
        source = {'type': 'function', 'function': {'name': 'manage_production_task', 'parameters': {
            'type': 'object', 'properties': {'action': {'type': 'string', 'enum': ['list', 'future']}}}}}
        catalog = {'manage_production_task.list': {'name': 'manage_production_task', 'action': 'list'},
                   'manage_production_task.future': {'name': 'manage_production_task', 'action': 'future'}}
        self.assertEqual(canonical_v2_schemas([source], catalog), [source])


if __name__ == '__main__':
    unittest.main()
