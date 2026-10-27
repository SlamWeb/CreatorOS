import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from creatoros.agent.plans import _validate_plan_inputs

import httpx
from sqlalchemy import select

from tests.agent_studio_support import serve
from tests.studio_review_fixtures import make_fixture
from creatoros.storage.models import AgentPlanStep, CreatorPlatform
from creatoros.storage import ContentRepository

class PlanTests(unittest.TestCase):
    def test_real_http_scoping_and_independent_failure(self):
        with tempfile.TemporaryDirectory() as root:
            db, _, _, app = make_fixture(Path(root))
            session = app.state.chat.create('review-lab')['id']
            ContentRepository(db).create_creator(creator_id='other-lab', display_name='other', platform=CreatorPlatform.XIAOHONGSHU)
            other = app.state.chat.create('other-lab')['id']
            with serve(app) as url, httpx.Client(base_url=url, timeout=10) as client:
                app.state.agent_plans.set_continuation_callback(None)
                path = f'/api/agent/sessions/{session}/plans'
                spec = {'request_id':'same-request', 'title':'独立步骤', 'steps':[
                    {'id':'bad','title':'错误参数','tool':'get_content_run','arguments':{'run_id':'missing'}},
                    {'id':'good','title':'读取选题','tool':'manage_topics','arguments':{'action':'list','series_id':'agent-notes'}},
                    {'id':'dependent','title':'等待失败项','tool':'get_content_run','arguments':{'run_id':'missing'},'depends_on':['bad']},
                ]}
                response=client.post(path, json=spec); self.assertEqual(response.status_code,200,response.text)
                plan=response.json(); identity=plan['id']
                duplicate=client.post(path,json=spec); self.assertEqual(duplicate.json()['id'],identity)
                wrong=client.get(f'/api/agent/sessions/{other}/plans/{identity}')
                self.assertEqual(wrong.status_code,404,wrong.text)
                deadline=time.monotonic()+15
                while time.monotonic()<deadline:
                    result=client.get(f'{path}/{identity}').json()
                    if result['status'] == 'needs_attention': break
                    time.sleep(.1)
                self.assertEqual(result['status'],'needs_attention',result)
                steps={x['id']:x for x in result['steps']}
                self.assertEqual(steps['good']['status'],'succeeded',result)
                self.assertEqual(steps['bad']['status'],'failed',result)
                self.assertEqual(steps['dependent']['status'],'blocked',result)
                claim=app.state.agent_plans.claim_continuation(session,'test')
                self.assertIsNotNone(claim)
                self.assertTrue(app.state.agent_plans.ack_continuation(identity,claim['event_id'],'test'))
                self.assertTrue(app.state.agent_plans.ack_continuation(identity,claim['event_id'],'test'))
            db.close()

    def test_real_http_waits_for_run_and_collects_only_that_run(self):
        with tempfile.TemporaryDirectory() as root:
            db, _, _, app = make_fixture(Path(root))
            sid = app.state.chat.create('review-lab')['id']
            with serve(app) as url, httpx.Client(base_url=url, timeout=10) as client:
                app.state.agent_plans.set_continuation_callback(None)
                path = f'/api/agent/sessions/{sid}/plans'
                spec = {'request_id':'precise', 'title':'产图收集', 'steps':[
                    {'id':'images','title':'产图','tool':'start_content_run',
                     'arguments':{'topic_id':'review-1','tasks':[{'title':'图片','brief':'测试图片'}]},
                     'wait_for_completion':True},
                    {'id':'collect','title':'收图','tool':'manage_media_collection',
                     'arguments':{'action':'create','name':'本轮','run_ids':{'$ref':'images.items.*.run_id'}},
                     'depends_on':['images']}]}
                response=client.post(path,json=spec)
                self.assertEqual(response.status_code,200,response.text)
                identity=response.json()['id']; deadline=time.monotonic()+25
                while time.monotonic()<deadline:
                    result=client.get(f'{path}/{identity}').json()
                    if result['status'] in {'needs_attention','completed'}: break
                    time.sleep(.1)
                self.assertEqual(result['status'],'completed',result)
                image, collection = result['steps']
                self.assertEqual(collection['result']['run_ids'], [image['result']['items'][0]['run_id']])
                self.assertIsNone(collection['result']['topic_id'])
            db.close()

    def test_plan_rejects_topic_wide_collection_for_parallel_images(self):
        source = SimpleNamespace(tool='start_content_run', wait_for_completion=True,
                                 result_json={'items':[{'run_id':'r1'}]})
        node = SimpleNamespace(tool='manage_media_collection', depends_on_json=['images'],
                               arguments_json={'run_ids': {'$ref':'images.items.*.run_id'}})
        with self.assertRaisesRegex(ValueError, '整个选题'):
            _validate_plan_inputs(None, node, {'action':'create','topic_id':'old', 'run_ids':['r1']}, {'images':source})
        video = SimpleNamespace(tool='start_content_run', depends_on_json=['images'],
                                arguments_json={'tasks':[{'kind':'video','inputs':{'collection_id':'old'}}]})
        with self.assertRaisesRegex(ValueError, '精确素材集'):
            _validate_plan_inputs(None, video, video.arguments_json, {'images':source})
        ordinary = SimpleNamespace(tool='manage_media_collection', depends_on_json=[], arguments_json={})
        _validate_plan_inputs(None, ordinary, {'action':'create', 'topic_id':'old'}, {})

    def test_interrupted_non_idempotent_step_never_replays(self):
        with tempfile.TemporaryDirectory() as root:
            db, _, _, app = make_fixture(Path(root))
            sid = app.state.chat.create('review-lab')['id']
            service = app.state.agent_plans
            service.set_continuation_callback(None)
            plan = service.create(sid, 'restart-test', '恢复测试', [
                {'id':'write','title':'修改选题','tool':'manage_topics',
                 'arguments':{'action':'update'},'depends_on':[]}], 'http://127.0.0.1:8765')
            with db.session() as session:
                row = session.get(AgentPlanStep, plan['id']+':write')
                row.status = 'running'
            service.start()
            try:
                deadline = time.monotonic()+5
                while time.monotonic()<deadline:
                    result=service.get(sid,plan['id'])
                    if result['status']=='needs_attention': break
                    time.sleep(.05)
                self.assertEqual(result['steps'][0]['status'],'unknown')
                self.assertFalse(result['steps'][0]['retryable'])
            finally:
                service.shutdown()
                db.close()

if __name__ == '__main__': unittest.main()
