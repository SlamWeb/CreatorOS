"""Isolated HTTP/SQLite continuation test; the provider is deterministic, not a real model."""
import tempfile
import time
import unittest
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace
from uuid import uuid4

import httpx

from creatoros.ai.types import ModelUsage, StreamEnd, TextDelta
from creatoros.storage.models import AgentPlan
from creatoros.web.app import create_app
from tests.agent_studio_support import serve
from tests.studio_review_fixtures import make_fixture


class ControlledProvider:
    def __init__(self, gate, started, calls, lock, tool_lists=None):
        self.gate, self.started, self.calls, self.lock = gate, started, calls, lock
        self.tool_lists = tool_lists
        self.client = SimpleNamespace(close=lambda: None)

    def stream(self, context):
        messages, tools = context.to_request()
        with self.lock:
            self.calls.append(messages)
            if self.tool_lists is not None:
                self.tool_lists.append({tool['function']['name'] for tool in tools})
        if not any(m.get('role') == 'system' and '[宿主通知：后台计划有结果' in str(m.get('content')) for m in messages):
            self.started.set()
            if not self.gate.wait(15):
                raise RuntimeError('test gate timed out')
        yield TextDelta('已收到结果。')
        yield StreamEnd('stop', ModelUsage(20, 5, 25))


def wait_for(predicate, timeout=15):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        value = predicate()
        if value:
            return value
        time.sleep(.05)
    raise AssertionError('timed out waiting for state')


class PlanContinuationTests(unittest.TestCase):
    def test_failed_plan_continuation_only_exposes_read_tools_and_can_reply(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db, runs, _, fixture_app = make_fixture(root)
            gate, started, calls, lock, tool_lists = Event(), Event(), [], Lock(), []
            gate.set()
            app = create_app(database=db, run_service=runs, chat_root=root / 'chats',
                             chat_provider_factory=lambda: ControlledProvider(gate, started, calls, lock, tool_lists))
            try:
                with serve(app) as url, httpx.Client(base_url=url, timeout=10, trust_env=False) as client:
                    sid = client.post('/api/agent/sessions', json={'creator_id':'review-lab'}).json()['id']
                    turn = client.post(f'/api/agent/sessions/{sid}/turns', json={
                        'request_id':str(uuid4()), 'text':'处理这个任务', 'expected_version':0})
                    self.assertEqual(turn.status_code, 202, turn.text)
                    wait_for(lambda: started.is_set() and app.state.chat._read(sid)['status'] != 'running')
                    path = f'/api/agent/sessions/{sid}/plans'
                    created = client.post(path, json={'request_id':str(uuid4()), 'title':'失败的读取', 'steps':[
                        {'id':'bad','title':'读取不存在的 Skill','tool':'get_producer_skill',
                         'arguments':{'skill_id':'missing'}}]})
                    self.assertEqual(created.status_code, 200, created.text)
                    pid = created.json()['id']
                    detail = lambda: client.get(f'{path}/{pid}').json()
                    wait_for(lambda: detail()['status'] == 'needs_attention' and detail()['continuation_state'] == 'done')
                    wait_for(lambda: app.state.chat._read(sid)['status'] != 'running')
                    doc = app.state.chat._read(sid)
                    with lock:
                        self.assertEqual(len(tool_lists), 2)
                        visible = tool_lists[-1]
                    self.assertTrue(visible)
                    self.assertTrue(visible <= {'get_content_run', 'get_producer_skill', 'get_creator_tasks',
                                               'list_creators', 'list_creator_series', 'list_producer_skills',
                                               'read_image', 'read_tool_result'}, visible)
                    self.assertFalse(visible & {'manage_plan', 'manage_topics', 'start_content_run',
                                                'manage_material', 'manage_media_collection'}, visible)
                    self.assertTrue(any(e.get('kind') == 'assistant' and '已收到结果' in e.get('text', '')
                                        for e in doc['entries']))
                    self.assertEqual(len(client.get(path).json()['items']), 1)
            finally:
                app.state.agent_plans.shutdown()
                app.state.chat.shutdown()
                db.close()

    def test_idle_continuation_is_deferred_and_deduplicated_without_fake_user(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db, runs, _, fixture_app = make_fixture(root)
            gate, started, calls, lock = Event(), Event(), [], Lock()
            app = create_app(database=db, run_service=runs, chat_root=root / 'chats',
                             chat_provider_factory=lambda: ControlledProvider(gate, started, calls, lock))
            try:
                with serve(app) as url, httpx.Client(base_url=url, timeout=10, trust_env=False) as client:
                    sid = client.post('/api/agent/sessions', json={'creator_id':'review-lab'}).json()['id']
                    turn = client.post(f'/api/agent/sessions/{sid}/turns', json={
                        'request_id':str(uuid4()), 'text':'请处理选题', 'expected_version':0})
                    self.assertEqual(turn.status_code, 202, turn.text)
                    self.assertTrue(started.wait(10))
                    path = f'/api/agent/sessions/{sid}/plans'
                    created = client.post(path, json={'request_id':str(uuid4()), 'title':'读取选题', 'steps':[
                        {'id':'read','title':'读取选题','tool':'manage_topics',
                         'arguments':{'action':'list','series_id':'agent-notes'}}]})
                    self.assertEqual(created.status_code, 200, created.text)
                    pid = created.json()['id']
                    detail = lambda: client.get(f'{path}/{pid}').json()
                    wait_for(lambda: detail()['status'] == 'completed')
                    self.assertEqual(detail()['continuation_state'], 'pending')
                    doc = app.state.chat._read(sid)
                    self.assertEqual(sum(r.get('kind') == 'plan_continuation' for r in doc['requests']), 0)
                    gate.set()
                    wait_for(lambda: detail()['continuation_state'] == 'done')
                    wait_for(lambda: app.state.chat._read(sid)['status'] != 'running')
                    # Simulate redelivery of the same durable event after acknowledgement.
                    with db.session() as database_session:
                        database_session.get(AgentPlan, pid).continuation_state = 'pending'
                    app.state.chat._resume_plans()
                    app.state.chat._resume_plans()
                    self.assertEqual(detail()['continuation_state'], 'done')
                    doc = app.state.chat._read(sid)
                    self.assertEqual(sum(r.get('kind') == 'plan_continuation' for r in doc['requests']), 1)
                    self.assertEqual([e['text'] for e in doc['entries'] if e['kind'] == 'user'], ['请处理选题'])
                    with lock:
                        observations = [m for call in calls for m in call if m.get('role') == 'system'
                                        and '[宿主通知：后台计划有结果' in str(m.get('content'))]
                    self.assertEqual(len(observations), 1)
                    self.assertIn(pid, observations[0]['content'])
                    self.assertEqual(len(calls), 2)
            finally:
                gate.set()
                app.state.agent_plans.shutdown()
                app.state.chat.shutdown()
                db.close()


if __name__ == '__main__':
    unittest.main()
