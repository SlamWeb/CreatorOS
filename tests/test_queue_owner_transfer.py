"""Isolated account transfer at the production admission boundary."""
from threading import Event, Thread, current_thread
from unittest import TestCase
from unittest.mock import patch

import httpx

from creatoros.runs.queue import ProductionQueue
from creatoros.storage import ContentRepository, CreatorPlatform
from creatoros.storage.models import ContentRevision
from creatoros.web.app import create_app
from tests import test_production_queue as fixtures
from tests.agent_studio_support import serve


class QueueOwnerTransferTests(TestCase):
    setUp = fixtures.ProductionQueueTests.setUp
    tearDown = fixtures.ProductionQueueTests.tearDown
    service = fixtures.ProductionQueueTests.service
    executor = fixtures.ProductionQueueTests.executor

    def assign(self, client, creator, revision, key):
        result = client.post('/api/series/series/assignment', json={
            'creator_id': creator, 'expected_revision': revision, 'request_id': key})
        self.assertEqual(result.status_code, 200, result.text)

    def test_transfer_preserves_active_work_blocks_pending_and_requires_explicit_resume(self):
        producer = fixtures.Producer(block=True)
        service = self.service(producer)
        ContentRepository(self.database).create_creator(creator_id='other', display_name='Fixture B', platform=CreatorPlatform.XIAOHONGSHU)
        executor = self.executor(service)
        app = create_app(database=self.database, run_service=service, run_executor=executor)
        first, old = service.create('topic-0'), service.create('topic-1')
        with serve(app) as base, httpx.Client(base_url=base, trust_env=False) as client:
            executor.submit(first.id, expected_version=first.version)
            self.assertTrue(producer.started.wait(5))
            queue = ProductionQueue(service)
            item = queue.enqueue(old.id, old.version)
            queue.transition(item.id, 'needs_attention', 'original failure evidence')
            executor.submit(old.id, expected_version=old.version)
            self.assertEqual(queue.snapshot(old.id)['history'][0]['last_error'], 'original failure evidence')
            self.assign(client, 'other', 1, 'transfer-to-b')
            with self.assertRaisesRegex(Exception, '账号不一致'):
                executor.submit(old.id, expected_version=old.version)
            self.assertEqual(queue.snapshot(old.id)['history'][0]['last_error'], 'original failure evidence')
            self.assertFalse(producer.release.is_set())
            producer.release.set()
            fixtures.wait(lambda: queue.snapshot(old.id)['status'] == 'needs_attention', timeout=15)
            self.assertIn('账号不一致', queue.snapshot(old.id)['last_error'])
            self.assertEqual(producer.calls, ['topic-0'])
            self.assertEqual(service.get(first.id).status.value, 'awaiting_approval')
            self.assertEqual(service.get(old.id).status.value, 'queued')
            self.assertFalse(service.repository.list_attempts(service.get_active_revision(old.id).id))
            new = service.create('topic-2')
            executor.submit(new.id, expected_version=new.version)
            fixtures.wait(lambda: not executor.is_submitted(new.id), timeout=15)
            self.assertEqual(service.get(new.id).status.value, 'awaiting_approval')
            self.assertEqual(producer.calls, ['topic-0', 'topic-2'])
            self.assign(client, 'creator', 2, 'transfer-back-a')
            self.assertEqual(queue.snapshot(old.id)['status'], 'needs_attention')
            self.assertEqual(producer.calls, ['topic-0', 'topic-2'])
            executor.submit(old.id, expected_version=old.version)
            fixtures.wait(lambda: not executor.is_submitted(old.id), timeout=15)
            self.assertEqual(service.get(old.id).status.value, 'awaiting_approval')
            self.assertEqual(producer.calls, ['topic-0', 'topic-2', 'topic-1'])
            history = queue.snapshot(old.id)['history']
            self.assertEqual(history[0]['last_error'], 'original failure evidence')
            self.assertIn('账号不一致', history[1]['last_error'])

    def test_revision_owner_mismatch_rejected_before_attempt(self):
        producer = fixtures.Producer()
        service = self.service(producer)
        run = service.create('topic-0')
        revision = service.get_active_revision(run.id)
        with self.database.session() as session:
            row = session.get(ContentRevision, revision.id)
            row.production_input_json = {**row.production_input_json, 'creator_id': 'wrong-owner'}
        with service.guard:
            with self.assertRaisesRegex(Exception, '归属'):
                service.claim(run.id, owner_id='local', expected_version=run.version)
        self.assertEqual(producer.calls, [])
        self.assertFalse(service.repository.list_attempts(revision.id))

    def test_transfer_serializes_with_dispatch_claim_and_does_not_hold_lock_during_producer(self):
        producer = fixtures.Producer(block=True)
        service = self.service(producer)
        ContentRepository(self.database).create_creator(creator_id='other', display_name='Fixture B', platform=CreatorPlatform.XIAOHONGSHU)
        executor = self.executor(service)
        app = create_app(database=self.database, run_service=service, run_executor=executor)
        run = service.create('topic-0')
        entered, release, moved, lock_waiting, armed = Event(), Event(), Event(), Event(), Event()
        original_lock = executor._lock
        class ObservedLock:
            def __enter__(self):
                if armed.is_set() and current_thread().name != "creatoros-production-queue":
                    lock_waiting.set()
                return original_lock.__enter__()
            def __exit__(self, *args):
                return original_lock.__exit__(*args)
        executor._lock = ObservedLock()
        original = service.claim
        def paused_claim(*args, **kwargs):
            entered.set()
            if not release.wait(10):
                raise RuntimeError('claim fixture timed out')
            return original(*args, **kwargs)
        with serve(app) as base, httpx.Client(base_url=base, trust_env=False, timeout=20) as client:
            with patch.object(service, 'claim', side_effect=paused_claim):
                executor.submit(run.id, expected_version=run.version)
                self.assertTrue(entered.wait(5))
                armed.set()
                response = {}
                def move():
                    response['value'] = client.post('/api/series/series/assignment', json={
                        'creator_id': 'other', 'expected_revision': 1, 'request_id': 'claim-race-transfer'})
                    moved.set()
                thread = Thread(target=move)
                thread.start()
                try:
                    self.assertTrue(lock_waiting.wait(5), 'transfer must reach the dispatch lock')
                    self.assertFalse(moved.is_set(), 'transfer must wait for dispatch admission')
                finally:
                    armed.clear()
                    release.set()
                self.assertTrue(moved.wait(10))
                thread.join(5)
                self.assertEqual(response['value'].status_code, 200, response['value'].text)
                self.assertTrue(producer.started.wait(5))
                self.assertFalse(producer.release.is_set(), 'ongoing producer must not be stopped')
            producer.release.set()
            fixtures.wait(lambda: not executor.is_submitted(run.id), timeout=15)
            self.assertEqual(service.get(run.id).status.value, 'awaiting_approval')
            self.assertEqual(producer.calls, ['topic-0'])
