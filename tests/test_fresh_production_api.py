"""Fresh production via real HTTP/SQLite; controlled producer avoids paid fault tests."""
import json
from datetime import timedelta
from pathlib import Path
import unittest

import httpx
from sqlalchemy import func, select

from creatoros.ai.types import ToolCall
from creatoros.context import RuntimeContext
from creatoros.storage import ContentRepository, CreatorPlatform, ContentRun, Topic, TopicSource
from creatoros.storage.models import ProductionQueueItem
from creatoros.tools import execute_tool_call
from creatoros.tools.studio import StartRunArgs
from creatoros.web.app import create_app
from tests.agent_studio_support import serve
from tests import test_production_queue as fixture


class FreshProductionAPITests(unittest.TestCase):
    setUp = fixture.ProductionQueueTests.setUp
    tearDown = fixture.ProductionQueueTests.tearDown
    service = fixture.ProductionQueueTests.service

    def test_fresh_http_replay_and_latest_projection_preserve_source(self):
        producer = fixture.Producer()
        runs = self.service(producer)
        source = runs.create('topic-0')
        runs.execute(source.id)
        app = create_app(database=self.database, run_service=runs)
        with serve(app) as base, httpx.Client(base_url=base, trust_env=False) as client:
            old = client.get(f'/api/runs/{source.id}').json()
            old_image = old['revisions'][0]['cards'][0]['url']
            payload = dict(topic_id='topic-0', mode='fresh', source_run_id=source.id,
                           expected_version=old['version'], request_id='fresh-http-one')
            result = client.post('/api/runs', json=payload)
            self.assertEqual(result.status_code, 201, result.text)
            fresh = result.json()
            self.assertNotEqual(fresh['id'], source.id)
            self.assertEqual(fresh['topic_id'], old['topic_id'])
            self.assertEqual(producer.calls, ['topic-0'])
            self.assertEqual(client.post('/api/runs', json=payload).json()['id'], fresh['id'])
            execute = client.post(f"/api/runs/{fresh['id']}/execute", json={'expected_version': fresh['version']})
            self.assertEqual(execute.status_code, 202, execute.text)
            fixture.wait(lambda: client.get(f"/api/runs/{fresh['id']}").json()['status'] == 'awaiting_approval')
            self.assertEqual(client.post('/api/runs', json=payload).json()['id'], fresh['id'])
            self.assertEqual(producer.calls, ['topic-0', 'topic-0'])
            # Editing an old record's audit timestamp must not restore it as the current Topic Run.
            with self.database.session() as session:
                stored = session.get(ContentRun, source.id)
                stored.updated_at += timedelta(days=1)
            current = client.get('/api/series/series/topics').json()['items'][0]
            self.assertEqual(current['existing_run_id'], fresh['id'])
            self.assertEqual(client.post('/api/runs', json={'topic_id': 'topic-0'}).json()['id'], fresh['id'])
            source_view = client.get(f'/api/runs/{source.id}').json()
            self.assertEqual(source_view['allowed_actions'], ['view'])
            self.assertEqual(source_view['latest_run_id'], fresh['id'])
            self.assertEqual(source_view['input_snapshot'], old['input_snapshot'])
            self.assertEqual(source_view['revisions'], old['revisions'])
            self.assertTrue(client.get(old_image).is_success)
            with self.database.session() as session:
                self.assertEqual(session.scalar(select(func.count()).select_from(ProductionQueueItem)), 1)
                self.assertEqual(session.scalar(select(func.count()).select_from(Topic)), 4)
            conflict = client.post('/api/runs', json={**payload, 'request_id': 'new-stale-source'})
            self.assertEqual(conflict.status_code, 409, conflict.text)

    def test_agent_fresh_scope_and_host_owned_request_identity(self):
        producer = fixture.Producer()
        runs = self.service(producer)
        source = runs.create('topic-0')
        runs.execute(source.id)
        repo = ContentRepository(self.database)
        repo.create_creator(creator_id='other', display_name='Other', platform=CreatorPlatform.XIAOHONGSHU)
        repo.create_series(series_id='other-series', creator_id='other', name='Other', description='', audience='', skill_name='knowledge-to-carousel')
        repo.add_topic(topic_id='other-topic', series_id='other-series', title='Other', source=TopicSource.MANUAL)
        other = runs.create('other-topic')
        app = create_app(database=self.database, run_service=runs)
        with serve(app) as base, httpx.Client(base_url=base, trust_env=False) as client:
            sid = client.post('/api/agent/sessions', json={'creator_id': 'creator'}).json()['id']
            version = runs.get(source.id).version
            payload = dict(topic_id='topic-0', mode='fresh', source_run_id=other.id,
                           expected_version=version, request_id='cross-account')
            denied = client.post('/api/runs', json=payload, headers={'x-creatoros-agent-session': sid})
            self.assertEqual(denied.status_code, 403, denied.text)
            with self.database.session() as session:
                self.assertEqual(session.scalar(select(func.count()).select_from(ContentRun)), 2)
            context = RuntimeContext(Path.cwd(), creator_id='creator', agent_session_id=sid,
                                     studio_url=base, user_request_id='one-user-turn')
            args = dict(topic_id='topic-0', mode='fresh', source_run_id=source.id, expected_version=version)
            call = ToolCall('fresh-1', 'start_content_run', json.dumps(args))
            first = execute_tool_call(call, context, model_requested=True)
            self.assertFalse(first.is_error, first.content)
            run_id = json.loads(first.content)['run_id']
            fixture.wait(lambda: client.get(f'/api/runs/{run_id}').json()['status'] == 'awaiting_approval')
            again = execute_tool_call(call, context, model_requested=True)
            self.assertFalse(again.is_error, again.content)
            self.assertEqual(json.loads(again.content)['run_id'], run_id)
            self.assertEqual(producer.calls, ['topic-0', 'topic-0'])

    def test_modes_are_explicit_and_cannot_bypass_batch_validation(self):
        from pydantic import ValidationError
        from creatoros.web.schemas import RunStartRequest
        for data in ({'topic_id': 'x', 'mode': 'fresh'},
                     {'topic_ids': ['x'], 'mode': 'fresh', 'source_run_id': 'r', 'expected_version': 1},
                     {'topic_id': 'x', 'source_run_id': 'r'}):
            with self.assertRaises(ValidationError):
                StartRunArgs.model_validate(data)
        with self.assertRaises(ValidationError):
            RunStartRequest(topic_id='x', mode='fresh', source_run_id='r', expected_version=1)


if __name__ == '__main__':
    unittest.main()
