"""Real loopback/SQLite and tool adapters; paid production replaced only for fault injection."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx
from sqlalchemy import func, select

from creatoros.ai.types import ToolCall
from creatoros.context import RuntimeContext
from creatoros.integrations.studio import StudioClient
from creatoros.storage import ContentRepository, CreatorPlatform, TopicSource, ContentRun, Series
from creatoros.storage.models import ProductionQueueItem
from creatoros.tools import execute_tool_call
from creatoros.tools.model_projection import project_model_data
from creatoros.tools.studio import StartRunArgs
from creatoros.web.app import create_app
from creatoros.web.chat_links import tool_links
from tests.agent_studio_support import serve
from tests import test_production_queue as queue_fixture

Producer, wait = queue_fixture.Producer, queue_fixture.wait


class BatchProductionTests(unittest.TestCase):
    setUp = queue_fixture.ProductionQueueTests.setUp
    tearDown = queue_fixture.ProductionQueueTests.tearDown
    service = queue_fixture.ProductionQueueTests.service
    executor = queue_fixture.ProductionQueueTests.executor

    def test_http_batch_partial_rejection_duplicate_and_failure_continue(self):
        producer = Producer(block=True, fail_first=True)
        service = self.service(producer)
        app = create_app(database=self.database, run_service=service, run_executor=self.executor(service))
        with serve(app) as base, httpx.Client(base_url=base, trust_env=False) as client:
            response = client.post('/api/runs/batch', json={'topic_ids': ['topic-0', 'missing', 'topic-1', 'topic-0']})
            self.assertEqual(response.status_code, 202, response.text)
            data = response.json()
            self.assertEqual(data['accepted_count'], 2)
            self.assertEqual(len(data['items']), 3)
            self.assertEqual(data['items'][1]['error_code'], 'not_found')
            self.assertTrue(producer.started.wait(2))
            second = data['items'][2]['run_id']
            detail = client.get(f'/api/runs/{second}').json()
            self.assertEqual(detail['production_queue']['status'], 'pending')
            self.assertEqual(detail['production_queue']['position'], 1)
            replay = client.post('/api/runs/batch', json={'topic_ids': ['topic-0', 'topic-1']}).json()
            self.assertEqual(replay['accepted_count'], 2)
            with self.database.session() as transaction:
                self.assertEqual(transaction.scalar(select(func.count()).select_from(ProductionQueueItem)), 2)
            producer.release.set()
            wait(lambda: client.get(f'/api/runs/{second}').json()['status'] == 'awaiting_approval')
            self.assertEqual(producer.calls, ['topic-0', 'topic-1'])
            again = client.post('/api/runs/batch', json={'topic_ids': ['topic-0', 'topic-1']}).json()
            self.assertEqual(again['accepted_count'], 0)
            self.assertEqual(producer.calls, ['topic-0', 'topic-1'])
            self.assertEqual(client.post('/api/runs/batch', json={'topic_ids': []}).status_code, 422)
            self.assertEqual(client.post('/api/runs/batch', json={'topic_ids': [' ']}).status_code, 422)

    def test_agent_batch_scope_preflight_and_real_tool_adapter(self):
        producer = Producer(block=True)
        repo = ContentRepository(self.database)
        repo.create_creator(creator_id='other', display_name='Other', platform=CreatorPlatform.XIAOHONGSHU)
        repo.create_series(series_id='other-series', creator_id='other', name='Other', description='', audience='', skill_name='knowledge-to-carousel')
        repo.add_topic(topic_id='other-topic', series_id='other-series', title='Other', source=TopicSource.MANUAL)
        app = create_app(database=self.database, run_service=self.service(producer))
        with serve(app) as base, httpx.Client(base_url=base, trust_env=False) as client:
            sid = client.post('/api/agent/sessions', json={'creator_id': 'creator'}).json()['id']
            headers = {'x-creatoros-agent-session': sid}
            denied = client.post('/api/runs/batch', headers=headers, json={'topic_ids': ['topic-0', 'other-topic']})
            self.assertEqual(denied.status_code, 403)
            with self.database.session() as transaction:
                self.assertEqual(transaction.scalar(select(func.count()).select_from(ContentRun)), 0)
            context = RuntimeContext(Path.cwd(), creator_id='creator', agent_session_id=sid, studio_url=base)
            result = execute_tool_call(ToolCall('batch', 'start_content_run', json.dumps(
                {'action': 'start', 'topic_ids': ['topic-0', 'topic-1']})), context, model_requested=True)
            self.assertFalse(result.is_error, result.content)
            data = json.loads(result.content)
            self.assertEqual(data['accepted_count'], 2)
            self.assertEqual(len(tool_links('start_content_run', result.content, studio_url=base)), 2)
            self.assertEqual(len(project_model_data('start_content_run', data)['items']), 2)
            producer.release.set()
            wait(lambda: len(producer.calls) == 2)

    def test_bad_skill_isolated_inside_owned_batch(self):
        producer = Producer()
        repo = ContentRepository(self.database)
        repo.create_series(series_id='broken', creator_id='creator', name='Broken', description='', audience='', skill_name='not-installed')
        repo.add_topic(topic_id='broken-topic', series_id='broken', title='Broken', source=TopicSource.MANUAL)
        app = create_app(database=self.database, run_service=self.service(producer))
        with serve(app) as base, httpx.Client(base_url=base, trust_env=False) as client:
            sid = client.post('/api/agent/sessions', json={'creator_id': 'creator'}).json()['id']
            response = client.post('/api/runs/batch', headers={'x-creatoros-agent-session': sid},
                json={'topic_ids': ['topic-0', 'broken-topic', 'topic-1']})
            self.assertEqual(response.status_code, 202, response.text)
            data = response.json()
            self.assertEqual(data['accepted_count'], 2)
            self.assertEqual(data['items'][1]['status'], 'rejected')
            wait(lambda: len(producer.calls) == 2)

    def test_uncertain_preclaim_marker_not_rearmed_by_batch(self):
        from creatoros.runs.queue import ProductionQueue
        producer = Producer()
        service = self.service(producer)
        run = service.create('topic-0')
        queue = ProductionQueue(service)
        item = queue.enqueue(run.id, run.version)
        queue.transition(item.id, 'running')  # Simulate crash before any claim.
        app = create_app(database=self.database, run_service=service)
        with serve(app) as base, httpx.Client(base_url=base, trust_env=False) as client:
            response = client.post('/api/runs/batch', json={'topic_ids': ['topic-0']}).json()
            self.assertEqual(response['accepted_count'], 0)
            self.assertEqual(client.get(f'/api/runs/{run.id}').json()['production_queue']['status'], 'needs_attention')
            self.assertEqual(producer.calls, [])

    def test_agent_batch_cannot_reauthorize_queue_changed_after_status_read(self):
        producer = Producer()
        service = self.service(producer)
        run = service.create('topic-0')
        executor = self.executor(service)
        app = create_app(database=self.database, run_service=service, run_executor=executor)
        with patch.object(executor, '_consume', side_effect=lambda: executor._stop.wait()):
            with serve(app) as base, httpx.Client(base_url=base, trust_env=False) as client:
                sid = client.post('/api/agent/sessions', json={'creator_id': 'creator'}).json()['id']
                context = RuntimeContext(Path.cwd(), creator_id='creator', agent_session_id=sid, studio_url=base)
                item = executor.queue.enqueue(run.id, run.version)
                pending = executor.queue_snapshot(run.id)
                call = ToolCall('batch', 'start_content_run', json.dumps({'action': 'start', 'topic_ids': ['topic-0']}))
                with ThreadPoolExecutor(max_workers=4) as pool:
                    repeats = list(pool.map(lambda _: execute_tool_call(call, context, model_requested=True), range(4)))
                self.assertTrue(all(not result.is_error for result in repeats))
                self.assertTrue(all(json.loads(result.content)['accepted_count'] == 1 for result in repeats))
                self.assertEqual(executor.queue_snapshot(run.id), pending)
                executor.queue.transition(item.id, 'running')
                running = executor.queue_snapshot(run.id)
                repeated = execute_tool_call(call, context, model_requested=True)
                self.assertEqual(json.loads(repeated.content)['accepted_count'], 1)
                self.assertEqual(executor.queue_snapshot(run.id), running)
                original = executor.submit
                original_state = []
                def change_after_batch_read(*args, **kwargs):
                    executor.queue.transition(item.id, 'needs_attention', 'controlled crash boundary')
                    original_state.append(executor.queue_snapshot(run.id))
                    return original(*args, **kwargs)
                with patch.object(executor, 'submit', side_effect=change_after_batch_read):
                    result = execute_tool_call(call, context, model_requested=True)
                self.assertFalse(result.is_error, result.content)
                self.assertEqual(json.loads(result.content)['accepted_count'], 0)
                self.assertEqual(executor.queue_snapshot(run.id), original_state[0])
                self.assertFalse(service.repository.list_attempts(service.get_active_revision(run.id).id))
                self.assertEqual(producer.calls, [])
                resumed = client.post(f'/api/runs/{run.id}/execute', json={'expected_version': run.version})
                self.assertEqual(resumed.status_code, 202, resumed.text)
                self.assertEqual(executor.queue_snapshot(run.id)['status'], 'pending')

    def test_public_start_preserves_attention_with_legacy_custom_key(self):
        producer = Producer()
        service = self.service(producer)
        run = service.create('topic-0', idempotency_key='daily:topic-0')
        executor = self.executor(service)
        app = create_app(database=self.database, run_service=service, run_executor=executor)
        with patch.object(executor, '_consume', side_effect=lambda: executor._stop.wait()):
            with serve(app) as base:
                item = executor.queue.enqueue(run.id, run.version)
                executor.queue.transition(item.id, 'needs_attention', '原记录')
                before = executor.queue_snapshot(run.id)
                studio = StudioClient(base)
                try:
                    found = studio.start('topic-0')
                finally:
                    studio.close()
                self.assertFalse(found['accepted'])
                self.assertEqual(executor.queue_snapshot(run.id), before)
                self.assertEqual(producer.calls, [])

    def test_batch_reports_attention_if_queue_changes_after_acceptance(self):
        producer = Producer()
        service = self.service(producer)
        run = service.create('topic-0')
        executor = self.executor(service)
        app = create_app(database=self.database, run_service=service, run_executor=executor)
        original = executor.submit
        with patch.object(executor, '_consume', side_effect=lambda: executor._stop.wait()):
            with serve(app) as base, httpx.Client(base_url=base, trust_env=False) as client:
                def stop_after_acceptance(*args, **kwargs):
                    receipt = original(*args, **kwargs)
                    queue = executor.queue_snapshot(run.id)
                    executor.queue.transition(queue['id'], 'needs_attention', 'controlled early stop')
                    return receipt
                with patch.object(executor, 'submit', side_effect=stop_after_acceptance):
                    response = client.post('/api/runs/batch', json={'topic_ids': ['topic-0']})
                self.assertEqual(response.status_code, 202, response.text)
                item = response.json()['items'][0]
                self.assertTrue(item['accepted'])
                self.assertEqual(item['status'], 'queued')
                self.assertEqual(item['error_code'], 'queue_needs_attention')
                self.assertIn('当前队列状态需要核对', item['error'])
                self.assertEqual(executor.queue_snapshot(run.id)['status'], 'needs_attention')
                self.assertEqual(producer.calls, [])

    def test_legacy_snapshot_and_empty_key_require_check_without_rewriting_records(self):
        producer = Producer()
        service = self.service(producer)
        run = service.create('topic-0', idempotency_key='old:topic-0')
        executor = self.executor(service)
        app = create_app(database=self.database, run_service=service, run_executor=executor)
        with patch.object(executor, '_consume', side_effect=lambda: executor._stop.wait()):
            with serve(app) as base, httpx.Client(base_url=base, trust_env=False) as client:
                item = executor.queue.enqueue(run.id, run.version)
                executor.queue.transition(item.id, 'needs_attention', '原记录')
                before = executor.queue_snapshot(run.id)
                old_snapshot = service.get(run.id).input_snapshot_json
                for incomplete in ({'topic_title': 'old incomplete snapshot'}, None, ['invalid']):
                    with self.database.session() as session:
                        session.get(ContentRun, run.id).input_snapshot_json = incomplete
                    current = service.get(run.id)
                    for initial in (True, False):
                        response = client.post(f'/api/runs/{run.id}/execute', json={
                            'expected_version': current.version, 'initial_only': initial})
                        self.assertEqual(response.status_code, 409, response.text)
                        self.assertIn('核对', response.text)
                        self.assertEqual(executor.queue_snapshot(run.id), before)
                with self.database.session() as session:
                    row = session.get(ContentRun, run.id)
                    row.input_snapshot_json = old_snapshot
                    row.idempotency_key = ''
                current = service.get(run.id)
                response = client.post(f'/api/runs/{run.id}/execute', json={
                    'expected_version': current.version, 'initial_only': True})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(executor.queue_snapshot(run.id), before)
                self.assertEqual(producer.calls, [])
                # An explicit recovery is still possible with a valid snapshot.
                resumed = client.post(f'/api/runs/{run.id}/execute', json={'expected_version': current.version})
                self.assertEqual(resumed.status_code, 202, resumed.text)
                self.assertEqual(executor.queue_snapshot(run.id)['status'], 'pending')

    def test_selected_run_first_authorizes_once_and_attempted_run_does_not_requeue(self):
        producer = Producer()
        service = self.service(producer)
        selected = service.create('topic-0', idempotency_key='selected:historical-0')
        attempted = service.create('topic-1')
        service.execute(attempted.id)
        with self.database.session() as session:
            session.get(ContentRun, attempted.id).status = 'queued'
        executor = self.executor(service)
        app = create_app(database=self.database, run_service=service, run_executor=executor)
        with patch.object(executor, '_consume', side_effect=lambda: executor._stop.wait()):
            with serve(app) as base, httpx.Client(base_url=base, trust_env=False) as client:
                response = client.post('/api/runs/batch', json={'topic_ids': ['topic-0', 'topic-1']})
                self.assertEqual(response.status_code, 202, response.text)
                data = response.json()
                self.assertEqual(data['accepted_count'], 1)
                self.assertEqual([item['run_id'] for item in data['items']], [selected.id, attempted.id])
                self.assertEqual(executor.queue_snapshot(selected.id)['status'], 'pending')
                self.assertIsNone(executor.queue_snapshot(attempted.id))
                self.assertEqual(producer.calls, ['topic-1'])

    def test_new_revision_initial_authorization_and_explicit_recovery(self):
        producer = Producer()
        service = self.service(producer)
        run = service.create('topic-0')
        service.execute(run.id)
        first = service.get(run.id)
        original = service.get_active_revision(run.id)
        self.assertTrue(service.repository.list_attempts(original.id))
        service.request_revision(run.id, '单独返工', expected_version=first.version)
        current = service.get(run.id)
        self.assertEqual(current.active_revision_number, 2)
        executor = self.executor(service)
        app = create_app(database=self.database, run_service=service, run_executor=executor)
        with patch.object(executor, '_consume', side_effect=lambda: executor._stop.wait()):
            with serve(app) as base, httpx.Client(base_url=base, trust_env=False) as client:
                path = f'/api/runs/{run.id}/execute'
                payload = {'expected_version': current.version, 'initial_only': True}
                first = client.post(path, json=payload)
                self.assertEqual(first.status_code, 202, first.text)
                self.assertTrue(first.json()['submission_accepted'])
                item = executor.queue_snapshot(run.id)
                self.assertEqual(item['status'], 'pending')
                executor.queue.transition(item['id'], 'needs_attention', '需核对')
                old = executor.queue_snapshot(run.id)
                replay = client.post(path, json=payload)
                self.assertEqual(replay.status_code, 202, replay.text)
                self.assertFalse(replay.json()['submission_accepted'])
                self.assertEqual(executor.queue_snapshot(run.id), old)
                self.assertEqual(len(service.repository.list_attempts(original.id)), 1)
                self.assertEqual(producer.calls, ['topic-0'])
                resumed = client.post(path, json={'expected_version': current.version})
                self.assertEqual(resumed.status_code, 202, resumed.text)
                self.assertEqual(executor.queue_snapshot(run.id)['status'], 'pending')

    def test_schema_requires_explicit_start_and_topic_list(self):
        self.assertEqual(StartRunArgs.model_validate({'action': 'start', 'topic_ids': ['one']}).topic_ids, ['one'])
        self.assertEqual(StartRunArgs.model_validate({'action': 'start', 'topic_ids': ['one', 'two']}).topic_ids, ['one', 'two'])
        for args in ({}, {'topic_ids': ['one']}, {'action': 'start', 'topic_id': 'one'},
                     {'action': 'start', 'topic_id': 'one', 'topic_ids': ['two']}, {'action': 'start', 'topic_ids': []}):
            with self.assertRaises(ValueError):
                StartRunArgs.model_validate(args)


if __name__ == '__main__':
    unittest.main()
