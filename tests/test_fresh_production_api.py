"""Fresh production via real HTTP/SQLite; controlled producer avoids paid fault tests."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Event
import unittest
from unittest.mock import patch

import httpx
from sqlalchemy import func, select

from creatoros.ai.types import ToolCall
from creatoros.context import RuntimeContext
from creatoros.integrations.studio import StudioClient, StudioClientError
from creatoros.storage import ContentRepository, CreatorPlatform, ContentRun, Topic, TopicSource
from creatoros.storage.models import ProductionQueueItem, Series
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
            args = dict(action='fresh', source_run_id=source.id, expected_version=version)
            call = ToolCall('fresh-1', 'start_content_run', json.dumps(args))
            first = execute_tool_call(call, context, model_requested=True)
            self.assertFalse(first.is_error, first.content)
            run_id = json.loads(first.content)['run_id']
            fixture.wait(lambda: client.get(f'/api/runs/{run_id}').json()['status'] == 'awaiting_approval')
            again = execute_tool_call(call, context, model_requested=True)
            self.assertFalse(again.is_error, again.content)
            self.assertEqual(json.loads(again.content)['run_id'], run_id)
            self.assertEqual(producer.calls, ['topic-0', 'topic-0'])

    def test_agent_fresh_replay_does_not_reauthorize_preclaim_crash(self):
        producer = fixture.Producer()
        runs = self.service(producer)
        source = runs.create('topic-0')
        runs.execute(source.id)
        app = create_app(database=self.database, run_service=runs)
        executor = app.state.executor
        release = Event()
        consume = executor._consume
        def paused_consumer():
            release.wait(15)
            consume()
        # A running marker with no Attempt is exactly the pre-claim crash
        # boundary: whether a worker actually ran is not known to the client.
        with patch.object(executor, '_consume', side_effect=paused_consumer):
            with serve(app) as base, httpx.Client(base_url=base, trust_env=False) as client:
                try:
                    sid = client.post('/api/agent/sessions', json={'creator_id': 'creator'}).json()['id']
                    context = RuntimeContext(Path.cwd(), creator_id='creator', agent_session_id=sid,
                                             studio_url=base, user_request_id='fresh-preclaim-turn')
                    args = dict(action='fresh', source_run_id=source.id, expected_version=runs.get(source.id).version)
                    call = ToolCall('fresh-replay', 'start_content_run', json.dumps(args))
                    first = execute_tool_call(call, context, model_requested=True)
                    self.assertFalse(first.is_error, first.content)
                    run_id = json.loads(first.content)['run_id']
                    pending = executor.queue_snapshot(run_id)
                    self.assertEqual(pending['status'], 'pending')
                    self.assertEqual(executor.queue_snapshot(run_id), pending)
                    replay = execute_tool_call(call, context, model_requested=True)
                    self.assertFalse(replay.is_error, replay.content)
                    self.assertEqual(executor.queue_snapshot(run_id), pending)
                    executor.queue.transition(pending['id'], 'running')
                    running = executor.queue_snapshot(run_id)
                    replay = execute_tool_call(call, context, model_requested=True)
                    self.assertFalse(replay.is_error, replay.content)
                    self.assertEqual(executor.queue_snapshot(run_id), running)
                    executor.queue.recover()
                    attention = executor.queue_snapshot(run_id)
                    self.assertEqual(attention['status'], 'needs_attention')
                    self.assertEqual(runs.get(run_id).status.value, 'queued')
                    self.assertFalse(runs.repository.list_attempts(runs.get_active_revision(run_id).id))
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        replays = list(pool.map(lambda _: execute_tool_call(call, context, model_requested=True), range(4)))
                    self.assertTrue(all(not replay.is_error for replay in replays))
                    self.assertTrue(all(json.loads(replay.content)['accepted'] is False for replay in replays))
                    self.assertTrue(all('相同请求不会再次授权' in replay.to_model_content() for replay in replays))
                    self.assertEqual(executor.queue_snapshot(run_id), attention)
                    # The public Studio client has the same fresh replay contract.
                    legacy = StudioClient(base, agent_session_id=sid)
                    try:
                        token = runs.get(run_id).idempotency_key.removeprefix('fresh:')
                        found = legacy.start('topic-0', mode='fresh', source_run_id=source.id,
                                             expected_version=args['expected_version'], request_id=token)
                        self.assertEqual(found['run_id'], run_id)
                        self.assertFalse(found['accepted'])
                        self.assertEqual(executor.queue_snapshot(run_id), attention)
                    finally:
                        legacy.close()
                    for status in ('failed', 'cancelled'):
                        executor.queue.transition(pending['id'], status, '原错误')
                        old = executor.queue_snapshot(run_id)
                        replay = execute_tool_call(call, context, model_requested=True)
                        self.assertFalse(replay.is_error, replay.content)
                        self.assertFalse(json.loads(replay.content)['accepted'])
                        self.assertEqual(executor.queue_snapshot(run_id), old)
                    self.assertEqual(producer.calls, ['topic-0'])
                    # Existing explicit recovery retains its original behavior.
                    resumed = client.post(f'/api/runs/{run_id}/execute', json={'expected_version': runs.get(run_id).version})
                    self.assertEqual(resumed.status_code, 202, resumed.text)
                    self.assertEqual(executor.queue_snapshot(run_id)['status'], 'pending')
                    release.set()
                    fixture.wait(lambda: runs.get(run_id).status.value == 'awaiting_approval')
                    self.assertEqual(producer.calls, ['topic-0', 'topic-0'])
                finally:
                    release.set()

    def test_fresh_execution_response_loss_remains_unknown_and_replay_is_idempotent(self):
        producer = fixture.Producer()
        runs = self.service(producer)
        source = runs.create('topic-0')
        runs.execute(source.id)
        app = create_app(database=self.database, run_service=runs)
        executor = app.state.executor
        actual_request = StudioClient.request
        lost = []
        def drop_response(client, method, path, **kwargs):
            value = actual_request(client, method, path, **kwargs)
            if method == 'POST' and path.endswith('/execute') and not lost:
                lost.append(True)
                raise StudioClientError('controlled lost response', 'studio_outcome_unknown')
            return value
        with patch.object(executor, '_consume', side_effect=lambda: executor._stop.wait()):
            with serve(app) as base, httpx.Client(base_url=base, trust_env=False) as client:
                sid = client.post('/api/agent/sessions', json={'creator_id': 'creator'}).json()['id']
                context = RuntimeContext(Path.cwd(), creator_id='creator', agent_session_id=sid,
                                         studio_url=base, user_request_id='fresh-response-lost')
                args = dict(action='fresh', source_run_id=source.id, expected_version=runs.get(source.id).version)
                call = ToolCall('fresh-lost', 'start_content_run', json.dumps(args))
                with patch.object(StudioClient, 'request', new=drop_response):
                    uncertain = execute_tool_call(call, context, model_requested=True)
                self.assertTrue(uncertain.is_error)
                self.assertEqual(uncertain.error_type, 'studio_outcome_unknown')
                receipt = json.loads(uncertain.content)
                self.assertTrue(receipt['outcome_unknown'])
                run_id = receipt['run_id']
                pending = executor.queue_snapshot(run_id)
                self.assertEqual(pending['status'], 'pending')
                replay = execute_tool_call(call, context, model_requested=True)
                self.assertFalse(replay.is_error, replay.content)
                self.assertEqual(json.loads(replay.content)['run_id'], run_id)
                self.assertEqual(executor.queue_snapshot(run_id), pending)
                self.assertEqual(producer.calls, ['topic-0'])

    def test_initial_authorization_rechecks_scope_and_version_in_transaction(self):
        producer = fixture.Producer()
        runs = self.service(producer)
        source = runs.create('topic-0')
        runs.execute(source.id)
        repo = ContentRepository(self.database)
        repo.create_creator(creator_id='other', display_name='Other', platform=CreatorPlatform.XIAOHONGSHU)
        app = create_app(database=self.database, run_service=runs)
        executor = app.state.executor
        with patch.object(executor, '_consume', side_effect=lambda: executor._stop.wait()):
            with serve(app) as base, httpx.Client(base_url=base, trust_env=False) as client:
                sid = client.post('/api/agent/sessions', json={'creator_id': 'creator'}).json()['id']
                other_sid = client.post('/api/agent/sessions', json={'creator_id': 'other'}).json()['id']
                headers = {'x-creatoros-agent-session': sid}
                payload = dict(topic_id='topic-0', mode='fresh', source_run_id=source.id,
                               expected_version=runs.get(source.id).version, request_id='fresh-scope-check')
                created = client.post('/api/runs', json=payload, headers=headers)
                self.assertEqual(created.status_code, 201, created.text)
                run = created.json()
                path = f"/api/runs/{run['id']}/execute"
                authorization = {'expected_version': run['version'], 'initial_only': True}
                self.assertEqual(client.post(path, json=authorization, headers=headers).status_code, 202)
                before = executor.queue_snapshot(run['id'])
                stale = client.post(path, json={**authorization, 'expected_version': 999}, headers=headers)
                self.assertEqual(stale.status_code, 409, stale.text)
                denied = client.post(path, json=authorization, headers={'x-creatoros-agent-session': other_sid})
                self.assertEqual(denied.status_code, 403, denied.text)
                invalid = client.post(f'/api/runs/{source.id}/execute',
                                      json={'expected_version': runs.get(source.id).version, 'initial_only': True},
                                      headers=headers)
                self.assertEqual(invalid.status_code, 422, invalid.text)
                original_enqueue = executor.queue.enqueue
                def reassign_then_enqueue(*args, **kwargs):
                    with self.database.session() as session:
                        session.get(Series, 'series').creator_id = 'other'
                    return original_enqueue(*args, **kwargs)
                # The request passed the HTTP scope guard before this move;
                # the write transaction must detect the changed owner itself.
                with patch.object(executor.queue, 'enqueue', side_effect=reassign_then_enqueue):
                    moved = client.post(path, json=authorization, headers=headers)
                self.assertEqual(moved.status_code, 403, moved.text)
                self.assertEqual(executor.queue_snapshot(run['id']), before)
                self.assertEqual(producer.calls, ['topic-0'])

    def test_fast_failure_does_not_claim_the_first_submission_never_ran(self):
        class FastFailure(fixture.Producer):
            def produce_to(self, **request):
                if self.calls:
                    self.calls.append(request['topic_id'])
                    raise RuntimeError('controlled immediate failure')
                return super().produce_to(**request)

        producer = FastFailure()
        runs = self.service(producer)
        source = runs.create('topic-0')
        runs.execute(source.id)
        app = create_app(database=self.database, run_service=runs)
        executor, queries = app.state.executor, app.state.queries
        actual_get = queries.get_run
        def after_queue_finishes(run_id):
            if run_id != source.id and executor.queue.snapshot(run_id) is not None:
                fixture.wait(lambda: executor.queue.snapshot(run_id)['status'] == 'failed')
            return actual_get(run_id)
        with serve(app) as base, httpx.Client(base_url=base, trust_env=False) as client:
            sid = client.post('/api/agent/sessions', json={'creator_id': 'creator'}).json()['id']
            context = RuntimeContext(Path.cwd(), creator_id='creator', agent_session_id=sid,
                                     studio_url=base, user_request_id='fresh-fast-failure')
            call = ToolCall('fresh-fast', 'start_content_run', json.dumps(dict(
                action='fresh', source_run_id=source.id, expected_version=runs.get(source.id).version)))
            with patch.object(queries, 'get_run', side_effect=after_queue_finishes):
                result = execute_tool_call(call, context, model_requested=True)
            self.assertFalse(result.is_error, result.content)
            data = json.loads(result.content)
            self.assertTrue(data['accepted'])
            self.assertEqual(data['production_queue']['status'], 'failed')
            self.assertIn('当前任务需要核对', result.to_model_content())
            self.assertNotIn('本次没有重新启动', result.to_model_content())
            self.assertEqual(producer.calls, ['topic-0', 'topic-0'])

    def test_modes_are_explicit_and_cannot_bypass_batch_validation(self):
        from pydantic import ValidationError
        from creatoros.web.schemas import RunStartRequest
        for data in ({'action': 'fresh', 'source_run_id': 'r'},
                     {'action': 'fresh', 'topic_ids': ['x'], 'source_run_id': 'r', 'expected_version': 1},
                     {'action': 'fresh', 'topic_id': 'x', 'source_run_id': 'r', 'expected_version': 1},
                     {'topic_id': 'x', 'mode': 'fresh', 'source_run_id': 'r', 'expected_version': 1}):
            with self.assertRaises(ValidationError):
                StartRunArgs.model_validate(data)
        with self.assertRaises(ValidationError):
            RunStartRequest(topic_id='x', mode='fresh', source_run_id='r', expected_version=1)


if __name__ == '__main__':
    unittest.main()
