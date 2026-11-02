"""Production observations are read from isolated SQLite, without running a producer."""

from pathlib import Path
import tempfile
import unittest

from sqlalchemy import select

from creatoros.runs.observation import observe_production
from creatoros.storage.models import ContentRevision, ContentRun, ContentRunStatus, ProductionTask
from tests.studio_review_fixtures import make_fixture


class ProductionObservationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.db, self.runs, self.producer, self.app = make_fixture(Path(self.temporary.name))

    def tearDown(self):
        self.app.state.agent_plans.shutdown()
        self.app.state.chat.shutdown()
        self.db.close()
        self.temporary.cleanup()

    def test_waits_for_every_run_and_preserves_completed_sibling_evidence(self):
        first = self.runs.create('review-1')
        second = self.runs.create('review-2')
        arguments = {'action': 'start', 'topic_ids': ['review-1', 'review-2']}
        receipt = {'items': [{'topic_id': 'review-1', 'run_id': first.id},
                             {'topic_id': 'review-2', 'run_id': second.id}]}
        with self.db.session() as session:
            session.get(ContentRun, first.id).status = ContentRunStatus.FAILED
            observed = observe_production(session, 'review-lab', arguments, receipt)
            self.assertIsNone(observed.terminal)
            self.assertEqual(observed.result['items'][0]['terminal_status'], 'failed')
            self.assertNotIn('terminal_status', receipt['items'][0])
            session.get(ContentRun, second.id).status = ContentRunStatus.AWAITING_APPROVAL
            revision = session.scalar(select(ContentRevision).where(ContentRevision.content_run_id == second.id))
            revision.artifact_digest = 'a' * 64
            observed = observe_production(session, 'review-lab', arguments, observed.result)
            self.assertEqual(observed.terminal[0], 'failed')
            self.assertEqual([x['terminal_status'] for x in observed.result['items']], ['failed', 'succeeded'])
            self.assertEqual(observed.result['items'][1]['artifact_digest'], 'a' * 64)
        self.assertEqual(self.producer.calls, 0)

    def test_waiting_task_without_run_stays_pending(self):
        with self.db.session() as session:
            session.add(ProductionTask(id='waiting-task', topic_id='review-1', title='等待素材',
                position=0, create_request_id='waiting', kind='video', definition_json={},
                dependency_json=[], dispatch_state='waiting'))
            session.flush()
            observed = observe_production(session, 'review-lab',
                {'action': 'tasks', 'topic_id': 'review-1'}, {'items': [{'id': 'waiting-task'}]})
            self.assertIsNone(observed.terminal)
            self.assertNotIn('run_id', observed.result['items'][0])
        self.assertEqual(self.producer.calls, 0)

    def test_other_account_cannot_observe_run(self):
        run = self.runs.create('review-1')
        with self.db.session() as session:
            observed = observe_production(session, 'another-account',
                {'action': 'start', 'topic_ids': ['review-1']},
                {'items': [{'topic_id': 'review-1', 'run_id': run.id}]})
            self.assertEqual(observed.terminal, ('failed', '制作任务不存在或账号范围不符。'))
        self.assertEqual(self.producer.calls, 0)

    def test_changed_artifact_digest_requires_recheck(self):
        run = self.runs.create('review-1')
        with self.db.session() as session:
            session.get(ContentRun, run.id).status = ContentRunStatus.AWAITING_APPROVAL
            revision = session.scalar(select(ContentRevision).where(ContentRevision.content_run_id == run.id))
            revision.artifact_digest = 'b' * 64
            observed = observe_production(session, 'review-lab',
                {'action': 'start', 'topic_ids': ['review-1']},
                {'items': [{'topic_id': 'review-1', 'run_id': run.id,
                            'revision_id': revision.id, 'artifact_digest': 'c' * 64}]})
            self.assertEqual(observed.terminal[0], 'failed')
            self.assertIn('产物版本发生变化', observed.result['items'][0]['error'])
        self.assertEqual(self.producer.calls, 0)


if __name__ == '__main__':
    unittest.main()
