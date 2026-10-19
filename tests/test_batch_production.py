"""Real loopback/SQLite and tool adapters; paid production replaced only for fault injection."""
import json
from pathlib import Path
import unittest

import httpx
from sqlalchemy import func, select

from creatoros.ai.types import ToolCall
from creatoros.context import RuntimeContext
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
                {'topic_ids': ['topic-0', 'topic-1']})), context, model_requested=True)
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

    def test_schema_requires_single_or_batch_not_both(self):
        self.assertEqual(StartRunArgs(topic_id='one').topic_id, 'one')
        self.assertEqual(StartRunArgs(topic_ids=['one', 'two']).topic_ids, ['one', 'two'])
        for args in ({}, {'topic_id': 'one', 'topic_ids': ['two']}, {'topic_ids': []}):
            with self.assertRaises(ValueError):
                StartRunArgs(**args)


if __name__ == '__main__':
    unittest.main()
