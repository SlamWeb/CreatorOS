"""Reference production contracts over real HTTP/SQLite, without paid models.

The explicit controlled legacy producer verifies request transport and durable
state. It does not test native Codex rendering or model tool-selection quality.
"""
import base64
import io
import json
from hashlib import sha256
from pathlib import Path
import unittest

import httpx
from PIL import Image
from sqlalchemy import func, select

from creatoros.ai.types import ToolCall
from creatoros.context import RuntimeContext
from creatoros.integrations.producer_skills import ProducerSkillCatalog, _digest, _write, skills_root_for
from creatoros.integrations.topic_references import TopicReferenceStore
from creatoros.integrations.topic_research import TopicResearchService
from creatoros.operations import OperationParseDecision, OperationParseResult, OperationPlan, PendingOperationService
from creatoros.storage import ContentRepository, ContentRun, CreatorPlatform, Database, PendingOperation, Topic, WriteReceipt
from creatoros.storage.models import ProductionQueueItem
from creatoros.tools import execute_tool_call
from creatoros.web.app import create_app
from tests.agent_studio_support import serve
from tests.smoke_topic_research import seed_batch
from tests import test_production_queue as fixture


def picture(color):
    out = io.BytesIO()
    Image.new('RGB', (28, 32), color).save(out, format='PNG')
    return out.getvalue()


class CapturingProducer(fixture.Producer):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.requests = []

    def produce_to(self, **request):
        self.requests.append(request)
        return super().produce_to(**request)


class ReferenceProductionAPITests(unittest.TestCase):
    setUp = fixture.ProductionQueueTests.setUp
    tearDown = fixture.ProductionQueueTests.tearDown
    service = fixture.ProductionQueueTests.service

    def counts(self):
        with self.database.session() as session:
            return tuple(session.scalar(select(func.count()).select_from(model))
                         for model in (Topic, ContentRun, ProductionQueueItem))

    def upload(self, client, series='series', *, color='orange', header=None):
        data = picture(color)
        response = client.post(f'/api/series/{series}/reference-images', headers=header,
                               json={'name': f'原始作品-{color}.png', 'data_base64': base64.b64encode(data).decode()})
        self.assertEqual(response.status_code, 201, response.text)
        meta = response.json()
        fetched = client.get(meta['url'], headers=header)
        self.assertEqual(fetched.content, data)
        return meta['id'], data

    def freeze(self, client, images, *, series='series', presentation=False, instruction='只借鉴情节，完整保留要求：零基础逐步解释，不要把我缩成一个标题。', header=None):
        result = client.post(f'/api/series/{series}/reference-inputs', headers=header, json={
            'image_ids': images, 'instructions': instruction, 'reference_presentation': presentation})
        self.assertEqual(result.status_code, 201, result.text)
        self.assertNotIn(str(self.root), result.text)
        return result.json()['id']

    def test_web_multireference_direct_replay_conflict_and_originals(self):
        producer = CapturingProducer(block=True)
        runs = self.service(producer)
        catalog = ProducerSkillCatalog(skills_root_for(self.database))
        skill = catalog.resolve('knowledge-to-carousel')
        skill_before = _digest(skill)
        baseline = self.counts()
        app = create_app(database=self.database, run_service=runs)
        with serve(app) as base, httpx.Client(base_url=base, trust_env=False, timeout=20) as client:
            first, first_data = self.upload(client)
            second, second_data = self.upload(client, color='blue')
            ref = self.freeze(client, [first, second])
            request = {'reference_id': ref, 'title': '借鉴作品讲常用英语', 'request_id': 'web-reference-one'}
            endpoint = '/api/series/series/reference-production'
            try:
                response = client.post(endpoint, json=request)
                self.assertEqual(response.status_code, 202, response.text)
                run = response.json()
                self.assertTrue(producer.started.wait(5))
                replay = client.post(endpoint, json=request)
                self.assertEqual(replay.status_code, 202, replay.text)
                self.assertEqual(replay.json()['id'], run['id'])
                changed = self.freeze(client, [first, second], presentation=True)
                for change in ({'title': '同键换标题'}, {'reference_id': changed}):
                    conflict = client.post(endpoint, json={**request, **change})
                    self.assertEqual(conflict.status_code, 409, conflict.text)
                self.assertEqual(self.counts(), tuple(number + 1 for number in baseline))
                supplied = producer.requests[0]['task_references']
                self.assertEqual(supplied['id'], ref)
                self.assertIn('完整保留要求', supplied['instructions'])
                self.assertFalse(supplied['reference_presentation'])
                self.assertEqual([Path(image['path']).read_bytes() for image in supplied['images']], [first_data, second_data])
                self.assertEqual(ContentRepository(self.database).get_topic(run['topic_id']).reference_id, ref)
            finally:
                producer.release.set()
            fixture.wait(lambda: client.get(f"/api/runs/{run['id']}").json()['status'] == 'awaiting_approval')
            self.assertEqual(client.post(endpoint, json=request).json()['id'], run['id'])
            self.assertEqual(len(producer.calls), 1)
            self.assertEqual(_digest(skill), skill_before, 'task references cannot modify the shared Skill')
            public_ref = client.get(f"/api/runs/{run['id']}").json()['input_snapshot']['task_references']
            self.assertEqual(public_ref['id'], ref)
            self.assertTrue(all('path' not in image for image in public_ref['images']))
            self.assertNotIn(str(self.root), json.dumps(public_ref, ensure_ascii=False))

    def test_research_candidate_generic_queue_keeps_reference_to_production(self):
        producer = CapturingProducer()
        runs = self.service(producer)
        research = TopicResearchService(self.database, ProducerSkillCatalog(skills_root_for(self.database)))
        app = create_app(database=self.database, run_service=runs, topic_research_service=research)
        with serve(app) as base, httpx.Client(base_url=base, trust_env=False, timeout=20) as client:
            image, data = self.upload(client, color='purple')
            ref = self.freeze(client, [image], presentation=True)
            record = seed_batch(research, 'series')
            record['task_references'] = TopicReferenceStore(self.database).load('series', ref).model_dump(mode='json')
            record['snapshot']['task_references'] = record['task_references']
            _write(research._path(record['id']), record)
            candidate = client.get('/api/topic-research/' + record['id']).json()['candidates'][0]
            response = client.post('/api/series/series/queue', json={'request_id': 'reference-candidate-queue', 'topics': [{
                'title': candidate['title'], 'brief': candidate['angle'], 'source': 'research', 'reference_id': candidate['reference_id']}]})
            self.assertEqual(response.status_code, 201, response.text)
            topic = response.json()['topic_ids'][0]
            self.assertEqual(ContentRepository(self.database).get_topic(topic).reference_id, ref)
            run = client.post('/api/runs', json={'topic_id': topic}).json()
            accepted = client.post(f"/api/runs/{run['id']}/execute", json={'expected_version': run['version']})
            self.assertEqual(accepted.status_code, 202, accepted.text)
            fixture.wait(lambda: client.get(f"/api/runs/{run['id']}").json()['status'] == 'awaiting_approval')
            supplied = producer.requests[0]['task_references']
            self.assertEqual(supplied['id'], ref)
            self.assertTrue(supplied['reference_presentation'])
            self.assertEqual(Path(supplied['images'][0]['path']).read_bytes(), data)

    def test_account_and_session_boundaries_reject_foreign_inputs_without_writes(self):
        producer = CapturingProducer()
        runs = self.service(producer)
        repo = ContentRepository(self.database)
        repo.create_creator(creator_id='other', display_name='Other', platform=CreatorPlatform.XIAOHONGSHU)
        repo.create_series(series_id='other-series', creator_id='other', name='Other', description='', audience='', skill_name='knowledge-to-carousel')
        app = create_app(database=self.database, run_service=runs)
        with serve(app) as base, httpx.Client(base_url=base, trust_env=False, timeout=20) as client:
            sid = client.post('/api/agent/sessions', json={'creator_id': 'creator'}).json()['id']
            other_sid = client.post('/api/agent/sessions', json={'creator_id': 'other'}).json()['id']
            header = {'x-creatoros-agent-session': sid}
            foreign_image, _ = self.upload(client, series='other-series', color='black')
            foreign_ref = self.freeze(client, [foreign_image], series='other-series')
            attachment = client.post(f'/api/agent/sessions/{other_sid}/attachments', json={
                'name': 'other.png', 'data_base64': base64.b64encode(picture('pink')).decode()}).json()['id']
            before = self.counts()
            denied = [
                client.post('/api/series/other-series/reference-inputs', headers=header, json={'image_ids': [foreign_image]}),
                client.get(f'/api/series/other-series/reference-images/{foreign_image}', headers=header),
                client.post('/api/series/series/reference-inputs', headers=header, json={'image_ids': [foreign_image]}),
                client.post('/api/series/series/reference-production', headers=header, json={
                    'reference_id': foreign_ref, 'title': 'forbidden', 'request_id': 'cross-ref-production'}),
                client.post(f'/api/agent/sessions/{sid}/image-actions/reference', headers=header, json={
                    'series_id': 'series', 'attachment_ids': [attachment]}),
                client.post(f'/api/agent/sessions/{sid}/image-actions/reference', headers=header, json={
                    'series_id': 'other-series', 'attachment_ids': [attachment]}),
                client.post(f'/api/agent/sessions/{other_sid}/image-actions/reference', headers=header, json={
                    'series_id': 'other-series', 'attachment_ids': [attachment]}),
                client.post('/api/series/series/queue', headers=header, json={'request_id': 'cross-ref-queue', 'topics': [{
                    'title': 'forbidden', 'reference_id': foreign_ref}]}),
            ]
            self.assertEqual(self.counts(), before)
            self.assertEqual(producer.calls, [])
            for response in denied:
                self.assertGreaterEqual(response.status_code, 400, response.text)
                self.assertLess(response.status_code, 500, response.text)
                self.assertNotIn(str(self.root), response.text)

    def test_agent_reference_tool_same_turn_reuses_one_run_and_keeps_original_images(self):
        producer = CapturingProducer()
        runs = self.service(producer)
        app = create_app(database=self.database, run_service=runs)
        with serve(app) as base, httpx.Client(base_url=base, trust_env=False, timeout=20) as client:
            sid = client.post('/api/agent/sessions', json={'creator_id': 'creator'}).json()['id']
            originals = [picture('green'), picture('yellow')]
            ids = []
            for index, data in enumerate(originals):
                response = client.post(f'/api/agent/sessions/{sid}/attachments', json={
                    'name': f'会话参考-{index}.png', 'data_base64': base64.b64encode(data).decode()})
                self.assertEqual(response.status_code, 201, response.text)
                ids.append(response.json()['id'])
            context = RuntimeContext(Path.cwd(), creator_id='creator', agent_session_id=sid,
                                     studio_url=base, user_request_id='one-reference-user-turn')
            before = self.counts()
            arguments = {'series_id': 'series', 'title': '这篇直接制作', 'instructions': '两张图共同借鉴，保留完整中文要求。',
                         'attachment_ids': ids, 'reference_presentation': True}
            results = []
            for call_id in ('reference-one', 'reference-repeated'):
                result = execute_tool_call(ToolCall(call_id, 'start_content_run', json.dumps(arguments)), context, model_requested=True)
                self.assertFalse(result.is_error, result.content)
                results.append(json.loads(result.content)['run_id'])
            self.assertEqual(results[0], results[1])
            fixture.wait(lambda: client.get(f'/api/runs/{results[0]}').json()['status'] == 'awaiting_approval')
            self.assertEqual(self.counts(), tuple(number + 1 for number in before))
            self.assertEqual(len(producer.calls), 1)
            supplied = producer.requests[0]['task_references']
            self.assertEqual(supplied['instructions'], arguments['instructions'])
            self.assertTrue(supplied['reference_presentation'])
            self.assertEqual([Path(item['path']).read_bytes() for item in supplied['images']], originals)

    def test_restart_preserves_reference_snapshot_and_legacy_production_is_unchanged(self):
        producer = CapturingProducer()
        runs = self.service(producer)
        app = create_app(database=self.database, run_service=runs)
        with serve(app) as base, httpx.Client(base_url=base, trust_env=False, timeout=20) as client:
            image, original = self.upload(client, color='cyan')
            ref = self.freeze(client, [image], instruction='重启后仍保留这段完整原始要求。')
            response = client.post('/api/series/series/reference-production', json={
                'reference_id': ref, 'title': '持久化作品', 'request_id': 'restart-reference-one'})
            self.assertEqual(response.status_code, 202, response.text)
            run_id = response.json()['id']
            fixture.wait(lambda: runs.get(run_id).status.value == 'awaiting_approval')
            frozen = runs.get(run_id).input_snapshot_json
            before = self.counts()
        self.database.close()
        self.database = Database(self.url)
        later = CapturingProducer()
        restarted = self.service(later)
        app = create_app(database=self.database, run_service=restarted)
        with serve(app) as base, httpx.Client(base_url=base, trust_env=False, timeout=20) as client:
            self.assertEqual(restarted.get(run_id).input_snapshot_json, frozen)
            refs = restarted.get(run_id).input_snapshot_json['task_references']
            self.assertEqual(refs['id'], ref)
            self.assertEqual(Path(refs['images'][0]['path']).read_bytes(), original)
            self.assertEqual(self.counts(), before)
            self.assertEqual(later.calls, [], 'restart must not regenerate completed content')
            legacy = client.post('/api/runs', json={'topic_id': 'topic-0'}).json()
            response = client.post(f"/api/runs/{legacy['id']}/execute", json={'expected_version': legacy['version']})
            self.assertEqual(response.status_code, 202, response.text)
            fixture.wait(lambda: restarted.get(legacy['id']).status.value == 'awaiting_approval')
            self.assertNotIn('task_references', later.requests[0])
            self.assertEqual(later.calls, ['topic-0'])

    def test_legacy_pending_token_queue_receipt_and_agent_request_identity_remain_compatible(self):
        runs = self.service(CapturingProducer())
        old_plan = {'schema_version': 1, 'operations': [{'action': 'add_topics', 'series_id': 'series', 'topics': [{
            'topic_id': 'historical-topic', 'title': '历史待确认选题', 'brief': None, 'source': 'manual'}]}]}
        initial = {'series': [f'topic-{number}' for number in range(4)]}
        old_token = sha256(json.dumps({'plan': old_plan, 'initial_orders': initial}, ensure_ascii=False,
                                      sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        service = PendingOperationService(self.database, parser=None)
        pending = service.persist_proposal('旧版本准备的待确认计划', OperationParseResult(
            decision=OperationParseDecision(status='ready', plan=OperationPlan.model_validate(old_plan)), usage=None),
            scope_series_id='series')
        self.assertEqual(pending.confirmation_token, old_token, 'adding optional references must not change old tokens')
        with self.database.session() as session:
            stored = session.get(PendingOperation, pending.id)
            stored.plan_json = old_plan  # Explicit historical on-disk JSON without the new optional field.
        app = create_app(database=self.database, run_service=runs)
        with serve(app) as base, httpx.Client(base_url=base, trust_env=False, timeout=20) as client:
            detail = client.get(f'/api/operations/{pending.id}').json()
            confirmed = client.post(f'/api/operations/{pending.id}/confirm', json={
                'expected_version': detail['version'], 'expected_revision': detail['revision'], 'confirmation_token': old_token})
            self.assertEqual(confirmed.status_code, 200, confirmed.text)
            self.assertIsNone(ContentRepository(self.database).get_topic('historical-topic').reference_id)
            legacy_queue = {'request_id': 'historical-queue-identity', 'topics': [{'title': '旧版入队回执'}]}
            response = client.post('/api/series/series/queue', json=legacy_queue)
            self.assertEqual(response.status_code, 201, response.text)
            original = response.json()
            with self.database.session() as session:
                receipt = session.get(WriteReceipt, legacy_queue['request_id'])
                operation = session.get(PendingOperation, receipt.response_json['operation_id'])
                plan = json.loads(json.dumps(operation.plan_json))
                for item in plan['operations'][0]['topics']:
                    item.pop('reference_id', None)
                operation.plan_json = plan
            before = self.counts()
            replay = client.post('/api/series/series/queue', json=legacy_queue)
            self.assertEqual(replay.status_code, 201, replay.text)
            self.assertTrue(replay.json()['deduplicated'])
            self.assertEqual(replay.json()['topic_ids'], original['topic_ids'])
            self.assertEqual(self.counts(), before)
            sid = client.post('/api/agent/sessions', json={'creator_id': 'creator'}).json()['id']
            context = RuntimeContext(Path.cwd(), creator_id='creator', agent_session_id=sid, studio_url=base,
                                     user_request_id='legacy-user-turn', session_file=self.root / 'legacy-session.json')
            old_items = [{'title': '旧Agent无图指令', 'brief': None, 'source': 'manual'}]
            identity = {'version': 1, 'tool': 'queue_topics', 'turn': 'legacy-user-turn', 'session': sid,
                        'creator': 'creator', 'series': 'series', 'topics': old_items}
            expected_id = sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
                                            separators=(',', ':')).encode()).hexdigest()
            # Historical queue identity remains compatible for host/internal replay;
            # new model requests use manage_topics and cannot call this hidden tool.
            result = execute_tool_call(ToolCall('legacy-queue', 'queue_topics', json.dumps({
                'series_id': 'series', 'topics': [{'title': '旧Agent无图指令'}]})), context, model_requested=False)
            self.assertFalse(result.is_error, result.content)
            self.assertEqual(json.loads(result.content)['request_id'], expected_id)


if __name__ == '__main__':
    unittest.main()
