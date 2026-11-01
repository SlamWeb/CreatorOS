"""Current tool contract, durable refs and old-plan isolation; no model calls."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
import unittest

import httpx
from sqlalchemy import select

from creatoros.agent.plans import PlanService, _validate_steps
from creatoros.runs.service import ContentRunError
from creatoros.storage.models import AgentPlan, AgentPlanStep, ContentRevision, ContentRun, ContentRunStatus
from creatoros.tools.definitions import tool_registry
from creatoros.tools.plans import _validate_argument_shape
from tests.agent_studio_support import serve
from tests.studio_review_fixtures import make_fixture


def image_step():
    return {'id': 'images', 'title': '制作图片', 'tool': 'start_content_run',
            'arguments': {'action': 'tasks', 'topic_id': 'review-1',
                          'tasks': [{'kind': 'image', 'title': '一张图', 'brief': '画一张图'}]}}


def referenced_video():
    return {'id': 'video', 'title': '制作视频', 'tool': 'start_content_run', 'depends_on': ['read'],
            'arguments': {'action': 'tasks', 'topic_id': 'review-1', 'tasks': [
                {'kind': 'video', 'title': '视频', 'brief': '按顺序剪辑',
                 'inputs': {'collection_id': {'$ref': 'read.id'}, 'bgm_reference': '用户分享原文'}}]}}


READ = {'id': 'read', 'title': '读取集合', 'tool': 'manage_media_collection',
        'arguments': {'action': 'get', 'collection_id': 'known'}}


class StaticPlanContractTests(unittest.TestCase):
    def test_references_keep_fixed_action_kind_and_reject_old_fields(self):
        tool = tool_registry['manage_plan']
        schema = tool.to_schema()['function']['parameters']
        self.assertEqual(schema['discriminator']['propertyName'], 'action')
        cases = [{'action': 'create', 'title': '计划', 'steps': [READ]}, {'action': 'list'},
                 {'action': 'get', 'plan_id': 'plan'},
                 {'action': 'update', 'plan_id': 'plan', 'expected_version': 1, 'steps': [READ]},
                 *[{'action': action, 'plan_id': 'plan', 'expected_version': 1} for action in ('pause', 'resume')],
                 *[{'action': action, 'plan_id': 'plan', 'expected_version': 1, 'step_id': 'read'}
                   for action in ('retry', 'skip')]]
        for arguments in cases:
            with self.subTest(plan_action=arguments['action']):
                reference = schema['discriminator']['mapping'][arguments['action']]
                branch = schema['$defs'][reference.rsplit('/', 1)[-1]]
                self.assertFalse(branch['additionalProperties'])
                self.assertEqual(set(branch['properties']), set(arguments))
                self.assertEqual(set(branch['required']), set(arguments))
                _validate_argument_shape(arguments, schema, schema)
                self.assertEqual(tool.parse_arguments(json.dumps(arguments))['action'], arguments['action'])
                invalid = [{key: value for key, value in arguments.items() if key != missing} for missing in arguments]
                invalid += [{**arguments, 'unexpected': True}, {**arguments, 'unexpected': None}]
                for bad in invalid:
                    with self.assertRaises(ValueError): _validate_argument_shape(bad, schema, schema)
                    with self.assertRaises(ValueError): tool.parse_arguments(json.dumps(bad))
        _validate_steps([READ, referenced_video()], 'review-lab')
        mutations = [
            lambda args: args.update(mode='start'),
            lambda args: args['tasks'][0].update(skills=[{'skill_id': 'knowledge-to-carousel', 'role': 'primary'}]),
            lambda args: args['tasks'][0].update(skill_overrides={}),
            lambda args: args['tasks'][0].pop('brief'),
            lambda args: args['tasks'][0].pop('kind'),
            lambda args: args['tasks'][0]['inputs'].update(bgm_material_id='a' * 64),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                video = referenced_video()
                mutate(video['arguments'])
                with self.assertRaises(ValueError): _validate_steps([READ, video], 'review-lab')

    def test_invalid_dependency_reference_rejected_before_save(self):
        for ref in [{'\u0024ref': 'missing.id'}, {'\u0024ref': 'read.'}, {'\u0024ref': 'read.id', 'other': 1}]:
            video = referenced_video()
            video['arguments']['tasks'][0]['inputs']['collection_id'] = ref
            with self.assertRaises(ValueError): _validate_steps([READ, video], 'review-lab')


class DurablePlanContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.db, self.runs, self.producer, self.app = make_fixture(Path(self.temporary.name))
        self.service = self.app.state.agent_plans
        self.sid = self.app.state.chat.create('review-lab')['id']

    def tearDown(self):
        self.service.shutdown()
        self.app.state.chat.shutdown()
        self.db.close()
        self.temporary.cleanup()

    def create(self, steps, request='new'):
        return self.service.create(self.sid, request, '计划', steps, 'http://127.0.0.1:8765')

    def test_real_http_save_references_and_reject_legacy_parameters(self):
        self.service.start_gate = lambda: False
        with serve(self.app) as url, httpx.Client(base_url=url, timeout=10, trust_env=False) as client:
            path = f'/api/agent/sessions/{self.sid}/plans'
            response = client.post(path, json={'request_id': 'http', 'title': '计划', 'steps': [READ, referenced_video()]})
            self.assertEqual(response.status_code, 200, response.text)
            plan = response.json()
            self.assertEqual(plan['tool_contract_version'], 2)
            self.assertFalse(plan['read_only'])
            self.assertEqual(plan['steps'][1]['arguments'], referenced_video()['arguments'])
            old = image_step()
            old['arguments'].pop('action')
            rejected = client.post(path, json={'request_id': 'old', 'title': '旧参数', 'steps': [old]})
            self.assertEqual(rejected.status_code, 422, rejected.text)
            self.assertEqual(len(client.get(path).json()['items']), 1)
        self.assertEqual(self.producer.calls, 0)

    def test_resolved_values_use_full_validator_before_dispatch(self):
        plan = self.create([READ, referenced_video()])
        with self.db.session() as session:
            source = session.get(AgentPlanStep, plan['id'] + ':read')
            source.status, source.result_json = 'succeeded', {'id': 17}  # controlled invalid dependency output
        self.service.advance()
        video = self.service.get(self.sid, plan['id'])['steps'][1]
        self.assertEqual(video['status'], 'failed')
        self.assertTrue(video['result']['_pre_dispatch'])
        self.assertEqual(video['arguments'], referenced_video()['arguments'])
        self.assertEqual(self.producer.calls, 0)

    def test_real_http_tasks_wait_for_own_run_and_keep_precise_collection_refs(self):
        self.service.set_continuation_callback(None)
        production = image_step()
        production['wait_for_completion'] = True
        collect = {'id': 'collect', 'title': '本次收图', 'tool': 'manage_media_collection',
                   'depends_on': ['images'], 'arguments': {'action': 'create', 'name': '本次',
                   'run_ids': {'$ref': 'images.items.*.run_id'}}}
        with serve(self.app) as url, httpx.Client(base_url=url, timeout=10, trust_env=False) as client:
            path = f'/api/agent/sessions/{self.sid}/plans'
            response = client.post(path, json={'request_id': 'wait-tasks', 'title': '产图收集',
                                              'steps': [production, collect]})
            self.assertEqual(response.status_code, 200, response.text)
            until = time.monotonic() + 20
            while time.monotonic() < until:
                plan = client.get(f'{path}/{response.json()["id"]}').json()
                if plan['status'] in {'completed', 'needs_attention'}: break
                time.sleep(.05)
            self.assertEqual(plan['status'], 'completed', plan)
            image, collection = plan['steps']
            self.assertEqual(image['result']['items'][0]['terminal_status'], 'succeeded')
            self.assertEqual(collection['result']['run_ids'], [image['result']['items'][0]['run_id']])
            self.assertIsNone(collection['result']['topic_id'])
        self.assertEqual(self.producer.calls, 1)

    def test_legacy_plan_stays_read_only_on_advance_restart_and_repeat_request(self):
        plan = self.create([image_step()])
        with self.db.session() as session:
            row = session.get(AgentPlan, plan['id'])
            row.request_digest, row.continuation_state = 'a' * 64, 'pending'
            step = session.get(AgentPlanStep, plan['id'] + ':images')
            step.status = 'running'
            step.arguments_json = {'topic_id': 'review-1', 'tasks': [{'title': '旧任务'}]}
        before = deepcopy(self.service.get(self.sid, plan['id']))
        self.assertTrue(before['read_only'])
        self.assertIsNone(before['tool_contract_version'])
        self.assertEqual(self.service.pending_continuation_sessions(), [])
        self.assertIsNone(self.service.claim_continuation(self.sid, 'worker'))
        self.service.advance()
        self.service.start()
        self.service.shutdown()
        for invoke in [lambda: self.create([image_step()]),
                       lambda: self.service.edit(self.sid, plan['id'], 1, [image_step()]),
                       lambda: self.service.action(self.sid, plan['id'], 1, 'pause')]:
            with self.assertRaises(ContentRunError) as caught: invoke()
            self.assertEqual(caught.exception.code, 'plan_contract_conflict')
        self.assertEqual(self.service.get(self.sid, plan['id']), before)
        self.assertEqual(self.producer.calls, 0)

    def test_start_batch_watch_uses_run_receipts_and_waits_for_every_sibling(self):
        first, second = self.runs.create('review-1'), self.runs.create('review-2')
        step = image_step()
        step['arguments'] = {'action': 'start', 'topic_ids': ['review-1', 'review-2']}
        step['wait_for_completion'] = True
        plan = self.create([step])
        with self.db.session() as session:
            session.get(ContentRun, first.id).status = ContentRunStatus.FAILED
            node = session.get(AgentPlanStep, plan['id'] + ':images')
            node.result_json = {'items': [{'topic_id': 'review-1', 'run_id': first.id},
                                           {'topic_id': 'review-2', 'run_id': second.id}]}
            owner = session.get(AgentPlan, plan['id'])
            self.assertIsNone(PlanService._watch(session, owner, node))
            self.assertEqual(node.result_json['items'][0]['terminal_status'], 'failed')
            session.get(ContentRun, second.id).status = ContentRunStatus.AWAITING_APPROVAL
            revision = session.scalar(select(ContentRevision).where(ContentRevision.content_run_id == second.id))
            revision.artifact_digest = 'b' * 64
            result = PlanService._watch(session, owner, node)
            self.assertEqual(result[0], 'failed')
            self.assertEqual([item['terminal_status'] for item in node.result_json['items']], ['failed', 'succeeded'])
        self.assertEqual(self.producer.calls, 0)

    def test_replanning_fresh_derives_scope_from_source_run(self):
        source = self.runs.create('review-1')
        original = image_step()
        original['arguments'] = {'action': 'start', 'topic_ids': ['review-1']}
        plan = self.create([original])
        with self.db.session() as session:
            session.get(AgentPlanStep, plan['id'] + ':images').status = 'succeeded'
        fresh = {'id': 'fresh', 'title': '重复重做', 'tool': 'start_content_run',
                 'arguments': {'action': 'fresh', 'source_run_id': source.id, 'expected_version': source.version}}
        with self.assertRaisesRegex(ContentRunError, '已有提交记录'):
            self.service.edit(self.sid, plan['id'], plan['version'], [original, fresh])
        self.assertEqual(len(self.service.get(self.sid, plan['id'])['steps']), 1)
        self.assertEqual(self.producer.calls, 0)


if __name__ == '__main__':
    unittest.main()
