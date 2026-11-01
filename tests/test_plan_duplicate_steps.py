"""Actual tool -> loopback HTTP -> SQLite; no model or production calls."""
from copy import deepcopy
from contextlib import ExitStack
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import unittest
from uuid import uuid4

import httpx

from creatoros.ai.types import ToolCall
from creatoros.context import RuntimeContext
from creatoros.tools.execution import execute_tool_call
from tests.agent_studio_support import serve
from tests.studio_review_fixtures import make_fixture


class DuplicatePlanStepTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        db, _, self.producer, app = make_fixture(root)
        self.addCleanup(db.close)
        app.state.agent_plans.set_continuation_callback(None)
        self.addCleanup(app.state.chat.shutdown)
        self.addCleanup(app.state.agent_plans.shutdown)
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.base = stack.enter_context(serve(app))
        self.client = stack.enter_context(httpx.Client(base_url=self.base, timeout=10, trust_env=False))
        sid = self.client.post('/api/agent/sessions', json={'creator_id': 'review-lab'}).json()['id']
        self.path = f'/api/agent/sessions/{sid}/plans'
        self.read_step = {'id': 'read', 'title': '原读取', 'tool': 'get_producer_skill',
                          'arguments': {'skill_id': 'missing--0000000000000000'}, 'depends_on': ['review']}
        response = self.client.post(self.path, json={'request_id': uuid4().hex, 'title': '暂停计划', 'steps': [
            {'id': 'review', 'title': '等待确认', 'tool': 'request_review', 'arguments': {'question': '先暂停'}},
            self.read_step]})
        self.assertEqual(response.status_code, 200, response.text)
        self.path += '/' + response.json()['id']
        deadline = monotonic() + 10
        while monotonic() < deadline:
            self.before = self.client.get(self.path).json()
            if self.before['status'] == 'paused':
                break
            sleep(.03)
        self.assertEqual(self.before['status'], 'paused')
        self.context = RuntimeContext(project_root=root, studio_url=self.base, creator_id='review-lab',
            agent_session_id=sid, plan_continuation_id=self.before['id'], allowed_tools=frozenset({'manage_plan'}))

    def update(self, steps):
        result = execute_tool_call(ToolCall(uuid4().hex, 'manage_plan', json.dumps({
            'action': 'update', 'plan_id': self.before['id'], 'expected_version': self.before['version'],
            'steps': steps})), context=self.context, model_requested=True)
        self.assertEqual(self.producer.calls, 0)
        return result

    def assert_rejected_unchanged(self, steps):
        result = self.update(steps)
        self.assertTrue(result.is_error, result)
        self.assertEqual(result.error_type, 'invalid_arguments')
        self.assertIn('重复', result.content)
        self.assertEqual(self.client.get(self.path).json(), self.before)

    def test_duplicate_existing_id_cannot_silently_choose_last_change(self):
        self.assert_rejected_unchanged([{**self.read_step, 'title': '第一个修改'},
                                        {**self.read_step, 'title': '最后一个修改'}])

    def test_duplicate_new_id_cannot_silently_discard_a_step(self):
        step = {**self.read_step, 'id': 'new-read'}
        self.assert_rejected_unchanged([step, {**step, 'title': '被覆盖的新步骤'}])

    def test_identical_duplicate_is_still_invalid(self):
        self.assert_rejected_unchanged([self.read_step, deepcopy(self.read_step)])

    def test_distinct_ids_update_and_keep_omitted_history(self):
        result = self.update([{**self.read_step, 'title': '改后的读取'},
                              {**self.read_step, 'id': 'another', 'title': '另一个读取'}])
        self.assertFalse(result.is_error, result)
        after = self.client.get(self.path).json()
        self.assertEqual([s['id'] for s in after['steps']], ['review', 'read', 'another'])
        self.assertEqual(after['steps'][0], self.before['steps'][0])
        self.assertEqual(after['steps'][1]['title'], '改后的读取')
        self.assertEqual(after['version'], self.before['version'] + 1)


if __name__ == '__main__':
    unittest.main()
