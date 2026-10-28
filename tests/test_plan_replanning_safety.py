"""Automatic continuation guard: real loopback HTTP and isolated SQLite, no model calls."""
from pathlib import Path
import json
import tempfile
import time
import unittest
from uuid import uuid4

import httpx
from sqlalchemy import func, select

from creatoros.ai.types import ToolCall
from creatoros.context import RuntimeContext
from creatoros.storage.models import AgentPlanStep, ProductionTask, ContentRun
from creatoros.tools.execution import execute_tool_call
from creatoros.tools.model_projection import project_model_content
from tests.agent_studio_support import serve
from tests.studio_review_fixtures import make_fixture


class ReplanningSafetyTests(unittest.TestCase):
    def test_partial_update_keeps_skipped_history_and_continues(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db, _, producer, app = make_fixture(root)
            app.state.agent_plans.set_continuation_callback(None)
            try:
                with serve(app) as url, httpx.Client(base_url=url, timeout=10, trust_env=False) as client:
                    sid = client.post('/api/agent/sessions', json={'creator_id': 'review-lab'}).json()['id']
                    path = f'/api/agent/sessions/{sid}/plans'
                    bad = {'id': 'bad', 'title': '读取不存在的 Skill', 'tool': 'get_producer_skill',
                           'arguments': {'skill_id': 'missing'}}
                    pending = {'id': 'list', 'title': '读取栏目选题', 'tool': 'manage_topics',
                               'arguments': {'action': 'list', 'series_id': 'agent-notes'}, 'depends_on': ['bad']}
                    created = client.post(path, json={'request_id': str(uuid4()), 'title': '故障后调整',
                                                     'steps': [bad, pending]})
                    self.assertEqual(created.status_code, 200, created.text)
                    pid = created.json()['id']
                    def read():
                        result = client.get(f'{path}/{pid}')
                        self.assertEqual(result.status_code, 200, result.text)
                        return result.json()
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        plan = read()
                        if plan['status'] == 'needs_attention': break
                        time.sleep(.05)
                    self.assertEqual([s['status'] for s in plan['steps']], ['failed', 'blocked'])
                    context = RuntimeContext(project_root=root, studio_url=url, creator_id='review-lab',
                        agent_session_id=sid, plan_continuation_id=pid, allowed_tools=frozenset({'manage_plan'}))
                    def call(args):
                        return execute_tool_call(ToolCall(str(uuid4()), 'manage_plan', json.dumps(args)),
                                                 context=context, model_requested=True)
                    skipped = call({'action': 'skip', 'plan_id': pid, 'step_id': 'bad',
                                    'expected_version': plan['version']})
                    self.assertFalse(skipped.is_error, skipped)
                    skipped_view = read()
                    self.assertEqual(skipped_view['steps'][0]['status'], 'skipped')
                    self.assertTrue(skipped_view['steps'][0]['error'])
                    pending['depends_on'] = []
                    changed = call({'action': 'update', 'plan_id': pid, 'expected_version': skipped_view['version'],
                                    'steps': [pending]})
                    self.assertFalse(changed.is_error, changed)
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        completed = read()
                        if completed['status'] == 'completed': break
                        time.sleep(.05)
                    self.assertEqual(completed['status'], 'completed', completed)
                    self.assertEqual([s['id'] for s in completed['steps']], ['bad', 'list'])
                    self.assertEqual([s['status'] for s in completed['steps']], ['skipped', 'succeeded'])
                    self.assertEqual(completed['steps'][0]['error'], skipped_view['steps'][0]['error'])
                    self.assertEqual(completed['steps'][0]['arguments']['skill_id'], 'missing')
                    self.assertEqual(producer.calls, 0)
            finally:
                app.state.agent_plans.shutdown()
                app.state.chat.shutdown()
                db.close()

    def test_model_projection_keeps_replanning_facts(self):
        arguments = {'topic_id': 't1', 'tasks': [{'title': '图片 A', 'kind': 'image',
                     'brief': '原始要求', 'skill_ids': ['skill-a']}], 'custom': {'key': [1, 2]}}
        step = {'id': 'images', 'title': '并行出图', 'tool': 'start_content_run',
                'arguments': arguments, 'depends_on': [], 'wait_for_completion': True,
                'status': 'failed', 'error': '部分失败', 'retryable': False, 'skippable': False,
                'result': {'items': [
                    {'id': 'task-1', 'run_id': 'run-1', 'terminal_status': 'succeeded', 'error': None},
                    {'id': 'task-2', 'run_id': 'run-2', 'terminal_status': 'failed', 'error': '图片不可读'},
                ]}}
        payload = {'id': 'plan-1', 'title': '计划', 'version': 3, 'status': 'needs_attention',
                   'pause_reason': 'failure', 'steps': [step]}
        projected = json.loads(project_model_content('manage_plan', json.dumps(payload, ensure_ascii=False)))
        self.assertEqual(projected['status'], 'needs_attention')
        self.assertEqual(projected['version'], 3)
        visible = projected['steps'][0]
        self.assertEqual(visible['arguments'], arguments)
        self.assertEqual(visible['status'], 'failed')
        self.assertFalse(visible['skippable'])
        self.assertFalse(visible['retryable'])
        self.assertEqual([(x['run_id'], x['terminal_status'], x['error'])
                          for x in visible['result']['items']],
                         [('run-1', 'succeeded', None), ('run-2', 'failed', '图片不可读')])

    def test_scoped_replanning_cannot_replace_unknown_production(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db, _, producer, app = make_fixture(root)
            app.state.agent_plans.start_gate = lambda: False
            try:
                with serve(app) as url, httpx.Client(base_url=url, timeout=10, trust_env=False) as client:
                    sid = client.post('/api/agent/sessions', json={'creator_id': 'review-lab'}).json()['id']
                    path = f'/api/agent/sessions/{sid}/plans'
                    image = {'id': 'images', 'title': '出图', 'tool': 'start_content_run',
                        'arguments': {'topic_id': 'review-1', 'tasks': [{'title': '图片 A'}]},
                        'depends_on': [], 'wait_for_completion': True}
                    read = {'id': 'read', 'title': '读取资料', 'tool': 'get_producer_skill',
                        'arguments': {'skill_id': 'missing'}, 'depends_on': []}
                    def create(name, steps):
                        response = client.post(path, json={'request_id': str(uuid4()), 'title': name, 'steps': steps})
                        self.assertEqual(response.status_code, 200, response.text)
                        return response.json()
                    plan = create('原计划', [image, read])
                    other = create('同一账号的另一计划', [read])
                    pid = plan['id']
                    with db.session() as session:
                        session.get(AgentPlanStep, pid + ':images').status = 'unknown'
                    plan = client.get(f'{path}/{pid}').json()
                    allowed = frozenset({'manage_plan', 'manage_topics', 'manage_material',
                        'manage_media_collection', 'manage_skill_library', 'get_producer_skill'})
                    context = RuntimeContext(project_root=root, studio_url=url, creator_id='review-lab',
                        agent_session_id=sid, plan_continuation_id=pid, allowed_tools=allowed)
                    def call(name, args):
                        return execute_tool_call(ToolCall(str(uuid4()), name, json.dumps(args)),
                                                 context=context, model_requested=True)
                    denied = [
                        call('manage_plan', {'action': 'get', 'plan_id': other['id']}),
                        call('manage_plan', {'action': 'create', 'title': '换计划重画', 'steps': [read]}),
                        call('manage_topics', {'action': 'remove', 'topic_id': 'review-1'}),
                        call('manage_media_collection', {'action': 'create', 'name': '不应创建'}),
                        call('start_content_run', {'topic_id': 'review-1', 'tasks': [{'title': '图片 A'}]}),
                    ]
                    for result in denied:
                        self.assertTrue(result.is_error, result)
                    replacement = {**image, 'id': 'different-id', 'title': '换标识重画'}
                    result = call('manage_plan', {'action': 'update', 'plan_id': pid,
                        'expected_version': plan['version'], 'steps': [image, read, replacement]})
                    self.assertTrue(result.is_error, result)
                    latest = client.get(f'{path}/{pid}').json()
                    self.assertEqual(len(latest['steps']), 2)
                    self.assertEqual(latest['steps'][0]['status'], 'unknown')
                    skipped = call('manage_plan', {'action': 'skip', 'plan_id': pid,
                        'expected_version': latest['version'], 'step_id': 'images'})
                    self.assertTrue(skipped.is_error, skipped)
                    retried = call('manage_plan', {'action': 'retry', 'plan_id': pid,
                        'expected_version': latest['version'], 'step_id': 'images'})
                    self.assertTrue(retried.is_error, retried)
                    # Replanning a never-started read is allowed and keeps the unknown step intact.
                    changed_read = {**read, 'title': '读取另一份资料', 'arguments': {'skill_id': 'another'}}
                    result = call('manage_plan', {'action': 'update', 'plan_id': pid,
                        'expected_version': latest['version'], 'steps': [image, changed_read]})
                    self.assertFalse(result.is_error, result)
                    latest = client.get(f'{path}/{pid}').json()
                    self.assertEqual(latest['steps'][0]['status'], 'unknown')
                    self.assertEqual(latest['steps'][1]['title'], changed_read['title'])
                    self.assertEqual(len(client.get(path).json()['items']), 2)
                    with db.session() as session:
                        self.assertEqual(session.scalar(select(func.count()).select_from(ProductionTask)), 0)
                        self.assertEqual(session.scalar(select(func.count()).select_from(ContentRun)), 0)
                    self.assertEqual(producer.calls, 0)

                    # Once images have succeeded, the already planned video may
                    # change only its inputs; this does not submit a new task.
                    video = {'id': 'video', 'title': '剪辑', 'tool': 'start_content_run',
                        'arguments': {'topic_id': 'review-1', 'tasks': [{'kind': 'video', 'title': '视频',
                            'inputs': {'collection_id': 'old', 'bgm_material_id': 'music'}}]},
                        'depends_on': [], 'wait_for_completion': True}
                    with db.session() as session:
                        session.get(AgentPlanStep, pid + ':images').status = 'succeeded'
                    # The user-originated plan already includes its pending video.
                    response = client.patch(f'{path}/{pid}', json={'expected_version': latest['version'],
                        'steps': [image, changed_read, video]})
                    self.assertEqual(response.status_code, 200, response.text)
                    latest = response.json()
                    revised = json.loads(json.dumps(video))
                    revised['arguments']['tasks'][0]['inputs']['collection_id'] = 'new'
                    result = call('manage_plan', {'action': 'update', 'plan_id': pid,
                        'expected_version': latest['version'], 'steps': [image, changed_read, revised]})
                    self.assertFalse(result.is_error, result)
                    self.assertEqual(client.get(f'{path}/{pid}').json()['steps'][2]['arguments']
                                     ['tasks'][0]['inputs']['collection_id'], 'new')
                    self.assertEqual(producer.calls, 0)
            finally:
                app.state.agent_plans.shutdown()
                app.state.chat.shutdown()
                db.close()


if __name__ == '__main__':
    unittest.main()
