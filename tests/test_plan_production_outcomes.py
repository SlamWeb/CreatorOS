"""Plan receipts and queue observation against isolated SQLite; no producer calls."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from creatoros.runs.observation import submission_outcome
from creatoros.integrations.production_identity import (reference_marker_id, reference_request_id, reference_topic_id,
    task_request_id)
from creatoros.tools.production_arguments import StartRunArgs, production_tasks_payload
from creatoros.storage.models import AgentPlanStep, ContentRevision, ContentRun, ContentRunStatus, ProductionQueueItem, ProductionTask, Topic, WriteReceipt
from creatoros.tools.results import ToolResult
from tests.studio_review_fixtures import make_fixture


class PlanProductionOutcomeTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(prefix="plan-production-outcomes-")
        self.addCleanup(tmp.cleanup)
        self.db, self.runs, self.producer, self.app = make_fixture(Path(tmp.name))
        self.addCleanup(self.db.close)
        self.addCleanup(self.app.state.chat.shutdown)
        self.addCleanup(self.app.state.agent_plans.shutdown)
        self.service = self.app.state.agent_plans
        self.sid = self.app.state.chat.create('review-lab')['id']
        self.arguments = {'action': 'start', 'topic_ids': ['review-1']}
        self.run = self.runs.create('review-1')
        self.revision = self.runs.get_active_revision(self.run.id)

    def plan(self, wait):
        plan = self.service.create(self.sid, f'request-{wait}', '生产并继续', [
            {'id': 'produce', 'title': '生产', 'tool': 'start_content_run',
             'arguments': self.arguments, 'wait_for_completion': wait},
            {'id': 'after', 'title': '下游', 'tool': 'get_content_run',
             'arguments': {'run_id': self.run.id}, 'depends_on': ['produce']},
        ], 'http://127.0.0.1:1')
        with self.db.session() as session:
            session.get(AgentPlanStep, plan['id'] + ':produce').status = 'running'
        return plan['id']

    def execute(self, plan_id, receipt):
        with patch('creatoros.agent.plans.execute_tool_call', return_value=ToolResult(content=json.dumps(receipt))) as tool:
            self.service._execute(plan_id, 'produce', self.sid, 'review-lab', 'http://127.0.0.1:1',
                                  'start_content_run', self.arguments)
        self.assertEqual(tool.call_count, 1)

    def queue(self, status):
        with self.db.session() as session:
            session.add(ProductionQueueItem(run_id=self.run.id, revision_id=self.revision.id,
                expected_version=self.run.version, status=status, last_error='原队列需要人工核对'))

    def test_not_waiting_rejected_queue_does_not_release_dependency(self):
        self.queue('needs_attention')
        plan_id = self.plan(False)
        receipt = {'accepted_count': 0, 'items': [{'topic_id': 'review-1', 'run_id': self.run.id,
            'status': 'rejected', 'accepted': False, 'error': '需要核对', 'error_code': 'queue_needs_attention'}]}
        self.execute(plan_id, receipt)
        self.service.advance()
        plan = self.service.get(self.sid, plan_id)
        self.assertEqual(plan['status'], 'needs_attention')
        self.assertEqual(plan['steps'][0]['status'], 'unknown')
        self.assertEqual(plan['steps'][1]['status'], 'blocked')
        self.assertEqual(plan['steps'][0]['result']['items'][0]['error_code'], 'queue_needs_attention')
        self.assertEqual(self.producer.calls, 0)

    def test_waiting_queue_needs_attention_does_not_wait_forever(self):
        self.queue('pending')
        plan_id = self.plan(True)
        receipt = {'accepted_count': 1, 'items': [{'topic_id': 'review-1', 'run_id': self.run.id,
            'status': 'queued', 'accepted': True}]}
        self.execute(plan_id, receipt)
        self.assertEqual(self.service.get(self.sid, plan_id)['steps'][0]['status'], 'waiting')
        with self.db.session() as session:
            item = session.query(ProductionQueueItem).filter_by(run_id=self.run.id).one()
            item.status = 'needs_attention'
            self.assertEqual(session.get(ContentRun, self.run.id).status.value, 'queued')
        self.service.advance()
        plan = self.service.get(self.sid, plan_id)
        self.assertEqual(plan['status'], 'needs_attention')
        self.assertEqual(plan['steps'][0]['status'], 'unknown')
        self.assertEqual(plan['steps'][1]['status'], 'blocked')
        self.assertEqual(plan['steps'][0]['result']['items'][0]['queue_status'], 'needs_attention')
        self.assertEqual(self.producer.calls, 0)

    def test_unknown_waiting_production_notifies_without_redispatch(self):
        self.queue('pending')
        plan_id = self.plan(True)
        receipt = {'accepted_count': 1, 'items': [{'topic_id': 'review-1', 'run_id': self.run.id,
            'status': 'queued', 'accepted': True}]}
        with patch('creatoros.agent.plans.execute_tool_call',
                   return_value=ToolResult(content=json.dumps(receipt))) as tool:
            self.service._execute(plan_id, 'produce', self.sid, 'review-lab', 'http://127.0.0.1:1',
                                  'start_content_run', self.arguments)
            with self.db.session() as session:
                session.query(ProductionQueueItem).filter_by(run_id=self.run.id).one().status = 'needs_attention'
            self.service.advance()
            plan = self.service.get(self.sid, plan_id)
            self.assertEqual((plan['status'], plan['steps'][0]['status']), ('needs_attention', 'unknown'))
            self.assertEqual(plan['steps'][1]['status'], 'blocked')
            event = self.service.claim_continuation(self.sid, 'test-worker')
            self.assertEqual(event['reason'], 'failure')
            self.assertEqual(event['steps'][0]['result']['items'][0]['run_id'], self.run.id)
            self.assertTrue(self.service.ack_continuation(plan_id, event['event_id'], 'test-worker'))
            for _ in range(3):
                self.service.advance()
            self.assertEqual(tool.call_count, 1)
            self.assertIsNone(self.service.claim_continuation(self.sid, 'other-worker'))
            self.assertEqual(self.service.get(self.sid, plan_id)['steps'][0]['status'], 'unknown')
        self.assertEqual(self.producer.calls, 0)

    def two_run_batch(self, wait):
        second = self.runs.create('review-2')
        revision = self.runs.get_active_revision(second.id)
        self.queue('needs_attention')
        with self.db.session() as session:
            session.add(ProductionQueueItem(run_id=second.id, revision_id=revision.id,
                expected_version=second.version, status='pending'))
        self.arguments = {'action': 'start', 'topic_ids': ['review-1', 'review-2']}
        plan_id = self.plan(wait)
        receipt = {'accepted_count': 1, 'original_note': '完整回执保留', 'items': [
            {'topic_id': 'review-1', 'run_id': self.run.id, 'status': 'rejected',
             'accepted': False, 'error': '需要核对', 'error_code': 'queue_needs_attention'},
            {'topic_id': 'review-2', 'run_id': second.id, 'status': 'queued', 'accepted': True},
        ]}
        self.execute(plan_id, receipt)
        return plan_id, second, revision, receipt

    def test_partial_batch_does_not_release_dependency_or_remove_accepted_sibling(self):
        plan_id, second, _revision, receipt = self.two_run_batch(False)
        self.service.advance()
        plan = self.service.get(self.sid, plan_id)
        self.assertEqual(plan['steps'][0]['status'], 'unknown')
        self.assertEqual(plan['steps'][1]['status'], 'blocked')
        self.assertEqual(plan['steps'][0]['result']['_submission_receipt'], receipt)
        with self.db.session() as session:
            self.assertEqual(session.query(ProductionQueueItem).filter_by(run_id=second.id).one().status, 'pending')
        self.assertEqual(self.producer.calls, 0)

    def test_waiting_batch_keeps_observing_other_sibling_before_unknown(self):
        plan_id, second, revision, receipt = self.two_run_batch(True)
        step = self.service.get(self.sid, plan_id)['steps'][0]
        self.assertEqual(step['status'], 'waiting')
        self.assertEqual(step['result']['items'][0]['terminal_status'], 'unknown')
        self.service.advance()
        self.assertEqual(self.service.get(self.sid, plan_id)['steps'][0]['status'], 'waiting')
        with self.db.session() as session:
            session.get(ContentRun, second.id).status = ContentRunStatus.AWAITING_APPROVAL
            session.get(ContentRevision, revision.id).artifact_digest = 'a' * 64
            session.query(ProductionQueueItem).filter_by(run_id=second.id).one().status = 'succeeded'
        self.service.advance()
        plan = self.service.get(self.sid, plan_id)
        self.assertEqual(plan['steps'][0]['status'], 'unknown')
        self.assertEqual(plan['steps'][1]['status'], 'blocked')
        self.assertEqual(plan['steps'][0]['result']['_submission_receipt'], receipt)
        self.assertEqual(plan['steps'][0]['result']['items'][1]['terminal_status'], 'succeeded')
        self.assertEqual(self.producer.calls, 0)

    def test_already_completed_receipt_with_accepted_false_can_be_reused(self):
        with self.db.session() as session:
            session.get(ContentRun, self.run.id).status = ContentRunStatus.AWAITING_APPROVAL
            session.get(ContentRevision, self.revision.id).artifact_digest = 'b' * 64
        plan_id = self.plan(False)
        self.execute(plan_id, {'accepted_count': 0, 'items': [{'topic_id': 'review-1', 'run_id': self.run.id,
            'status': 'awaiting_approval', 'accepted': False}]})
        self.assertEqual(self.service.get(self.sid, plan_id)['steps'][0]['status'], 'succeeded')
        self.service.advance()
        self.assertEqual(self.service.get(self.sid, plan_id)['steps'][1]['status'], 'running')
        self.assertEqual(self.producer.calls, 0)

    def test_reference_false_accepted_with_real_queue_is_valid(self):
        self.queue('pending')
        request_id, ref = 'reference-fixture', 'example-reference'
        topic_id = reference_topic_id('agent-notes', request_id)
        with self.db.session() as session:
            source = session.get(Topic, 'review-1')
            session.add(Topic(id=topic_id, series_id='agent-notes', title=source.title, brief=source.brief,
                              source=source.source, status=source.status, position=22, reference_id=ref))
            run = session.get(ContentRun, self.run.id)
            run.topic_id, run.idempotency_key = topic_id, 'content:' + topic_id
            session.add(WriteReceipt(request_id=request_id, operation='queue_topics', resource_id='agent-notes',
                origin='agent', response_json={'creator_id': 'review-lab', 'reference_production': {'run_id': run.id}}))
            session.flush()
            args = {'action': 'reference', 'series_id': 'agent-notes', 'title': self.run.input_snapshot_json['topic_title']}
            receipt = {'run_id': self.run.id, 'status': 'queued', 'accepted': False, 'production_queue': None}
            identity = {'request_id': request_id, 'reference_id': ref}
            self.assertEqual(submission_outcome(session, 'review-lab', args, receipt, identity), ('succeeded', None))
            # Old successful receipts lacked reference_production; the same topic key remains definitive.
            session.get(WriteReceipt, request_id).response_json = {'creator_id': 'review-lab'}
            session.flush()
            self.assertEqual(submission_outcome(session, 'review-lab', args, receipt, identity), ('succeeded', None))
            self.assertEqual(submission_outcome(session, 'review-lab', args, receipt,
                {**identity, 'reference_id': 'different-reference'})[0], 'unknown')
        self.assertEqual(self.producer.calls, 0)

    def test_unrelated_run_is_not_a_reference_or_fresh_receipt(self):
        self.queue('pending')
        with self.db.session() as session:
            receipt = {'run_id': self.run.id, 'status': 'queued', 'accepted': False}
            reference = submission_outcome(session, 'review-lab',
                {'action': 'reference', 'series_id': 'agent-notes', 'title': self.run.input_snapshot_json['topic_title']}, receipt)
            fresh = submission_outcome(session, 'review-lab',
                {'action': 'fresh', 'source_run_id': self.run.id, 'expected_version': self.run.version}, receipt)
            self.assertEqual(reference[0], 'unknown')
            self.assertEqual(fresh[0], 'unknown')

    def test_real_fresh_source_event_and_queue_authorize_its_receipt(self):
        with self.db.session() as session:
            session.get(ContentRun, self.run.id).status = ContentRunStatus.FAILED
        version = self.runs.get(self.run.id).version
        fresh = self.runs.restart_fresh(self.run.id, expected_version=version, request_id='fixture-fresh')
        revision = self.runs.get_active_revision(fresh.id)
        with self.db.session() as session:
            session.add(ProductionQueueItem(run_id=fresh.id, revision_id=revision.id,
                expected_version=fresh.version, status='pending'))
            session.flush()
            receipt = {'run_id': fresh.id, 'status': 'queued', 'accepted': False}
            arguments = {'action': 'fresh', 'source_run_id': self.run.id, 'expected_version': version}
            identity = {'request_id': 'fixture-fresh'}
            self.assertEqual(submission_outcome(session, 'review-lab', arguments, receipt, identity), ('succeeded', None))
            self.assertEqual(submission_outcome(session, 'review-lab',
                {**arguments, 'expected_version': version + 1}, receipt, identity)[0], 'unknown')
            self.assertEqual(submission_outcome(session, 'review-lab', arguments, receipt,
                {'request_id': 'different-request'})[0], 'unknown')
        self.assertEqual(self.producer.calls, 0)

    def test_valid_nonwaiting_submission_unblocks_dependent_without_waiting_for_image(self):
        self.queue('pending')
        plan_id = self.plan(False)
        self.execute(plan_id, {'accepted_count': 1, 'items': [{'topic_id': 'review-1', 'run_id': self.run.id,
            'status': 'queued', 'accepted': True}]})
        self.service.advance()
        steps = self.service.get(self.sid, plan_id)['steps']
        self.assertEqual(steps[0]['status'], 'succeeded')
        self.assertEqual(steps[1]['status'], 'running')
        with self.db.session() as session:
            self.assertEqual(session.query(ProductionQueueItem).filter_by(run_id=self.run.id).one().status, 'pending')
        self.assertEqual(self.producer.calls, 0)

    def test_waiting_dependent_task_without_run_is_valid(self):
        with self.db.session() as session:
            session.add(ProductionTask(id='dependent-task', topic_id='review-1', title='等待图片',
                position=0, create_request_id='waiting', kind='video', definition_json={},
                dependency_json=[], dispatch_state='waiting'))
            session.add(WriteReceipt(request_id='waiting', operation='queue_topics', resource_id='review-1',
                origin='agent', response_json={'creator_id': 'review-lab', 'kind': 'production_tasks',
                                               'task_ids': ['dependent-task']}))
            session.flush()
            outcome = submission_outcome(session, 'review-lab',
                {'action': 'tasks', 'topic_id': 'review-1', 'tasks': [{'kind': 'video'}]},
                {'topic_id': 'review-1', 'items': [{'id': 'dependent-task', 'run_id': None, 'status': 'waiting'}]},
                {'request_id': 'waiting'})
            self.assertEqual(outcome, ('succeeded', None))

    def test_foreign_account_run_cannot_be_used_as_submitted_work(self):
        self.queue('pending')
        with self.db.session() as session:
            status, message = submission_outcome(session, 'another-account', self.arguments,
                {'accepted_count': 1, 'items': [{'topic_id': 'review-1', 'run_id': self.run.id,
                    'status': 'queued', 'accepted': True}]})
            self.assertEqual(status, 'unknown')
            self.assertIn('账号', message)
        self.assertEqual(self.producer.calls, 0)

    def test_accepted_receipt_with_error_still_blocks_followup(self):
        self.queue('needs_attention')
        plan_id = self.plan(False)
        self.execute(plan_id, {'accepted_count': 1, 'items': [{'topic_id': 'review-1', 'run_id': self.run.id,
            'status': 'queued', 'accepted': True, 'error': '刚提交后即需核对', 'error_code': 'queue_needs_attention'}]})
        self.service.advance()
        plan = self.service.get(self.sid, plan_id)
        self.assertEqual(plan['steps'][0]['status'], 'unknown')
        self.assertEqual(plan['steps'][1]['status'], 'blocked')

    def test_full_receipt_is_preserved_even_when_model_projection_is_smaller(self):
        self.queue('needs_attention')
        plan_id = self.plan(False)
        receipt = {'accepted_count': 0, 'items': [{'topic_id': 'review-1', 'run_id': self.run.id,
            'status': 'rejected', 'accepted': False, 'error': '需要核对', 'error_code': 'queue_needs_attention',
            'evidence': {'original': '保留'}}]}
        with patch('creatoros.agent.plans.execute_tool_call', return_value=ToolResult(
                content=json.dumps(receipt), model_content='{"items": []}')):
            self.service._execute(plan_id, 'produce', self.sid, 'review-lab', 'http://127.0.0.1:1',
                                  'start_content_run', self.arguments)
        step = self.service.get(self.sid, plan_id)['steps'][0]
        self.assertEqual(step['status'], 'unknown')
        self.assertEqual(step['result']['_submission_receipt'], receipt)

    def test_task_needing_attention_without_run_is_unknown_not_retryable(self):
        with self.db.session() as session:
            session.add(ProductionTask(id='uncertain-task', topic_id='review-1', title='待核对',
                position=0, create_request_id='unknown', kind='image', definition_json={},
                dependency_json=[], dispatch_state='needs_attention', last_error='队列结果不明'))
            session.add(WriteReceipt(request_id='unknown', operation='queue_topics', resource_id='review-1',
                origin='agent', response_json={'creator_id': 'review-lab', 'kind': 'production_tasks',
                                               'task_ids': ['uncertain-task']}))
            session.flush()
            outcome = submission_outcome(session, 'review-lab',
                {'action': 'tasks', 'topic_id': 'review-1', 'tasks': [{'kind': 'image'}]},
                {'topic_id': 'review-1', 'items': [{'id': 'uncertain-task', 'run_id': None,
                                                   'status': 'needs_attention'}]}, {'request_id': 'unknown'})
            self.assertEqual(outcome[0], 'unknown')

    def test_malformed_success_receipts_are_unknown_instead_of_crashing_or_replaying(self):
        plan_id = self.plan(True)
        receipt = {'accepted_count': 1, 'items': [{'topic_id': 'review-1', 'run_id': {'not': 'an id'},
            'status': 'queued', 'accepted': True}]}
        self.execute(plan_id, receipt)
        self.service.advance()
        plan = self.service.get(self.sid, plan_id)
        self.assertEqual(plan['steps'][0]['status'], 'unknown')
        self.assertEqual(plan['steps'][0]['result']['_submission_receipt'], receipt)
        self.assertEqual(plan['steps'][1]['status'], 'blocked')
        self.assertEqual(self.producer.calls, 0)

    def test_batch_normalizes_whitespace_and_duplicate_ids_like_api(self):
        self.queue('pending')
        self.arguments = {'action': 'start', 'topic_ids': [' review-1 ', 'review-1']}
        plan_id = self.plan(False)
        self.execute(plan_id, {'accepted_count': 1, 'items': [{'topic_id': 'review-1', 'run_id': self.run.id,
            'status': 'queued', 'accepted': True}]})
        step = self.service.get(self.sid, plan_id)['steps'][0]
        self.assertEqual(step['status'], 'succeeded')
        self.assertEqual(step['arguments']['topic_ids'], [' review-1 ', 'review-1'])

    def test_task_receipt_must_belong_to_exact_plan_request(self):
        self.arguments = {'action': 'tasks', 'topic_id': ' review-1 ', 'tasks': [
            {'kind': 'image', 'title': ' 一张图 ', 'brief': ' 画面要求 '}]}
        plan_id = self.plan(False)
        normalized = StartRunArgs.model_validate(self.arguments)
        request_id = task_request_id(self.sid, f'plan:{plan_id}:produce', 'review-lab', 'review-1',
                                     production_tasks_payload(normalized))
        receipt = {'topic_id': 'review-1', 'items': [{'id': 'actual-task', 'run_id': None, 'status': 'waiting'}]}
        with self.db.session() as session:
            session.add(ProductionTask(id='actual-task', topic_id='review-1', title='一张图',
                position=0, create_request_id=request_id, kind='image', definition_json={},
                dependency_json=[], dispatch_state='waiting'))
            session.add(WriteReceipt(request_id=request_id, operation='queue_topics', resource_id='review-1',
                origin='agent', response_json={'creator_id': 'review-lab', 'kind': 'production_tasks',
                                               'task_ids': ['actual-task']}))
        self.execute(plan_id, receipt)
        self.assertEqual(self.service.get(self.sid, plan_id)['steps'][0]['status'], 'succeeded')
        self.assertEqual(self.producer.calls, 0)

    def test_same_topic_old_task_cannot_impersonate_new_request(self):
        self.arguments = {'action': 'tasks', 'topic_id': 'review-1', 'tasks': [
            {'kind': 'image', 'title': '新图', 'brief': '新内容'}]}
        plan_id = self.plan(True)
        with self.db.session() as session:
            session.add(ProductionTask(id='old-task', topic_id='review-1', title='旧视频', position=0,
                create_request_id='old-request', kind='video', definition_json={},
                dependency_json=[], dispatch_state='waiting'))
        self.execute(plan_id, {'topic_id': 'review-1', 'items': [
            {'id': 'old-task', 'run_id': None, 'status': 'waiting'}]})
        step = self.service.get(self.sid, plan_id)['steps'][0]
        self.assertEqual(step['status'], 'unknown')
        self.service.advance()
        self.assertEqual(self.service.get(self.sid, plan_id)['steps'][1]['status'], 'blocked')
        self.assertEqual(self.producer.calls, 0)

    def test_wrong_task_identity_with_pending_sibling_never_releases_dependency(self):
        self.arguments = {'action': 'tasks', 'topic_id': 'review-1', 'tasks': [
            {'kind': 'image', 'title': '第一张', 'brief': '内容一'},
            {'kind': 'image', 'title': '第二张', 'brief': '内容二'}]}
        plan_id = self.plan(True)
        with self.db.session() as session:
            for index, state in enumerate(('needs_attention', 'waiting')):
                session.add(ProductionTask(id=f'other-{index}', topic_id='review-1', title='其他请求', position=index,
                    create_request_id='old-request', kind='image', definition_json={}, dependency_json=[], dispatch_state=state))
        receipt = {'topic_id': 'review-1', 'items': [
            {'id': 'other-0', 'run_id': None, 'status': 'needs_attention'},
            {'id': 'other-1', 'run_id': None, 'status': 'waiting'}]}
        self.execute(plan_id, receipt)
        self.assertEqual(self.service.get(self.sid, plan_id)['steps'][0]['status'], 'unknown')
        # A stale waiting row cannot drop the original failed identity check.
        with self.db.session() as session:
            session.get(AgentPlanStep, plan_id + ':produce').status = 'waiting'
        self.service.advance()
        self.assertEqual(self.service.get(self.sid, plan_id)['steps'][0]['status'], 'unknown')
        self.assertEqual(self.service.get(self.sid, plan_id)['steps'][1]['status'], 'blocked')
        self.assertEqual(self.producer.calls, 0)

    def test_reference_plan_uses_original_attachment_marker_and_receipt(self):
        self.queue('pending')
        title, ref = self.run.input_snapshot_json['topic_title'], 'reference-from-attachment-a'
        self.arguments = {'action': 'reference', 'series_id': ' agent-notes ', 'title': f' {title} ',
                          'attachment_ids': ['attachment-a'], 'instructions': ' 简单参考 '}
        plan_id = self.plan(False)
        request_id = reference_request_id(self.sid, 'agent-notes', ref, title)
        marker_id = reference_marker_id(self.sid, 'agent-notes', ['attachment-a'], '简单参考', False)
        marker = self.app.state.chat.root / 'reference-requests' / f'{marker_id}.json'
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({'reference_id': ref}), encoding='utf-8')
        topic_id = reference_topic_id('agent-notes', request_id)
        with self.db.session() as session:
            source = session.get(Topic, 'review-1')
            session.add(Topic(id=topic_id, series_id='agent-notes', title=title, brief=source.brief,
                source=source.source, status=source.status, position=22, reference_id=ref))
            run = session.get(ContentRun, self.run.id)
            run.topic_id, run.idempotency_key = topic_id, 'content:' + topic_id
            session.add(WriteReceipt(request_id=request_id, operation='queue_topics', resource_id='agent-notes',
                origin='agent', response_json={'creator_id': 'review-lab', 'reference_production': {'run_id': run.id}}))
        receipt = {'run_id': self.run.id, 'status': 'queued', 'accepted': False}
        self.execute(plan_id, receipt)
        self.assertEqual(self.service.get(self.sid, plan_id)['steps'][0]['status'], 'succeeded')
        # Different requested attachment, same title and a real old receipt: not this request.
        self.arguments['attachment_ids'] = ['attachment-b']
        second_plan = self.plan(True)
        new_marker = reference_marker_id(self.sid, 'agent-notes', ['attachment-b'], '简单参考', False)
        (marker.parent / f'{new_marker}.json').write_text(
            json.dumps({'reference_id': 'reference-from-attachment-b'}), encoding='utf-8')
        self.execute(second_plan, receipt)
        self.assertEqual(self.service.get(self.sid, second_plan)['steps'][0]['status'], 'unknown')
        self.assertEqual(self.producer.calls, 0)

    def test_legacy_waiting_start_remains_observable_without_redispatch(self):
        self.queue('pending')
        plan_id = self.plan(True)
        receipt = {'accepted_count': 1, 'items': [{'topic_id': 'review-1', 'run_id': self.run.id,
            'status': 'queued', 'accepted': True}]}
        with self.db.session() as session:
            step = session.get(AgentPlanStep, plan_id + ':produce')
            step.status, step.result_json = 'waiting', receipt
        self.service.advance()
        self.assertEqual(self.service.get(self.sid, plan_id)['steps'][0]['status'], 'waiting')
        with self.db.session() as session:
            session.get(ContentRun, self.run.id).status = ContentRunStatus.AWAITING_APPROVAL
            session.get(ContentRevision, self.revision.id).artifact_digest = 'a' * 64
            session.query(ProductionQueueItem).filter_by(run_id=self.run.id).one().status = 'succeeded'
        self.service.advance()
        self.assertEqual(self.service.get(self.sid, plan_id)['steps'][0]['status'], 'succeeded')
        self.assertEqual(self.producer.calls, 0)

    def test_legacy_waiting_tasks_without_request_identity_need_review(self):
        self.arguments = {'action': 'tasks', 'topic_id': 'review-1', 'tasks': [
            {'kind': 'image', 'title': '图', 'brief': '内容'}]}
        plan_id = self.plan(True)
        with self.db.session() as session:
            session.add(ProductionTask(id='legacy-task', topic_id='review-1', title='图', position=0,
                create_request_id='legacy', kind='image', definition_json={}, dependency_json=[], dispatch_state='waiting'))
            step = session.get(AgentPlanStep, plan_id + ':produce')
            step.status, step.result_json = 'waiting', {'topic_id': 'review-1', 'items': [
                {'id': 'legacy-task', 'run_id': None, 'status': 'waiting'}]}
        self.service.advance()
        self.assertEqual(self.service.get(self.sid, plan_id)['steps'][0]['status'], 'unknown')
        self.assertEqual(self.producer.calls, 0)

    def test_legacy_waiting_task_with_original_receipt_keeps_waiting(self):
        self.arguments = {'action': 'tasks', 'topic_id': 'review-1', 'tasks': [
            {'kind': 'image', 'title': '图', 'brief': '内容'}]}
        plan_id = self.plan(True)
        request_id = task_request_id(self.sid, f'plan:{plan_id}:produce', 'review-lab', 'review-1',
            production_tasks_payload(StartRunArgs.model_validate(self.arguments)))
        with self.db.session() as session:
            session.add(ProductionTask(id='legacy-valid', topic_id='review-1', title='图', position=0,
                create_request_id=request_id, kind='image', definition_json={}, dependency_json=[], dispatch_state='waiting'))
            session.add(WriteReceipt(request_id=request_id, operation='queue_topics', resource_id='review-1',
                origin='agent', response_json={'creator_id': 'review-lab', 'kind': 'production_tasks',
                                               'task_ids': ['legacy-valid']}))
            step = session.get(AgentPlanStep, plan_id + ':produce')
            step.status, step.result_json = 'waiting', {'topic_id': 'review-1', 'items': [
                {'id': 'legacy-valid', 'run_id': None, 'status': 'waiting'}]}
        self.service.advance()
        step = self.service.get(self.sid, plan_id)['steps'][0]
        self.assertEqual(step['status'], 'waiting')
        self.assertEqual(step['result']['_submission_identity']['request_id'], request_id)
        self.assertEqual(self.producer.calls, 0)

    def test_restart_recovers_original_run_from_write_receipt_without_dispatch(self):
        self.queue('pending')
        self.arguments = {'action': 'tasks', 'topic_id': 'review-1', 'tasks': [
            {'kind': 'image', 'title': '图', 'brief': '内容'}]}
        plan_id = self.plan(True)
        request_id = task_request_id(self.sid, f'plan:{plan_id}:produce', 'review-lab', 'review-1',
            production_tasks_payload(StartRunArgs.model_validate(self.arguments)))
        with self.db.session() as session:
            session.add(ProductionTask(id='recover-task', topic_id='review-1', title='图', position=0,
                create_request_id=request_id, kind='image', definition_json={}, dependency_json=[], dispatch_state='dispatched'))
            session.flush()
            original = session.get(ContentRun, self.run.id)
            original.task_id, original.idempotency_key = 'recover-task', 'task:recover-task'
            session.add(ContentRun(id='later-fresh', topic_id='review-1', task_id='recover-task',
                idempotency_key='fresh:later', status=ContentRunStatus.QUEUED, input_snapshot_json=original.input_snapshot_json))
            session.add(WriteReceipt(request_id=request_id, operation='queue_topics', resource_id='review-1',
                origin='agent', response_json={'creator_id': 'review-lab', 'kind': 'production_tasks',
                                               'task_ids': ['recover-task']}))
        with patch('creatoros.agent.plans.Thread'), patch('creatoros.agent.plans.ThreadPoolExecutor'), \
             patch('creatoros.agent.plans.execute_tool_call') as tool:
            self.service.start()
            step = self.service.get(self.sid, plan_id)['steps'][0]
            self.assertEqual(step['status'], 'waiting')
            self.assertTrue(step['result']['_recovered_from_receipt'])
            self.assertEqual(step['result']['items'][0]['run_id'], self.run.id)
            self.assertNotEqual(step['result']['items'][0]['run_id'], 'later-fresh')
            tool.assert_not_called()
        self.assertEqual(self.producer.calls, 0)

    def test_restart_of_nonwaiting_task_preserves_accepted_semantics(self):
        self.arguments = {'action': 'tasks', 'topic_id': 'review-1', 'tasks': [
            {'kind': 'image', 'title': '图', 'brief': '内容'}]}
        plan_id = self.plan(False)
        request_id = task_request_id(self.sid, f'plan:{plan_id}:produce', 'review-lab', 'review-1',
            production_tasks_payload(StartRunArgs.model_validate(self.arguments)))
        with self.db.session() as session:
            session.add(ProductionTask(id='async-task', topic_id='review-1', title='图', position=0,
                create_request_id=request_id, kind='image', definition_json={}, dependency_json=[], dispatch_state='waiting'))
            session.add(WriteReceipt(request_id=request_id, operation='queue_topics', resource_id='review-1',
                origin='agent', response_json={'creator_id': 'review-lab', 'kind': 'production_tasks',
                                               'task_ids': ['async-task']}))
        with patch('creatoros.agent.plans.Thread'), patch('creatoros.agent.plans.ThreadPoolExecutor'), \
             patch('creatoros.agent.plans.execute_tool_call') as tool:
            self.service.start()
            self.assertEqual(self.service.get(self.sid, plan_id)['steps'][0]['status'], 'succeeded')
            self.service.advance()
            self.assertEqual(self.service.get(self.sid, plan_id)['steps'][1]['status'], 'running')
            tool.assert_not_called()
        self.assertEqual(self.producer.calls, 0)

    def test_restart_bad_frozen_snapshot_is_unknown_not_service_failure(self):
        self.arguments = {'action': 'tasks', 'topic_id': 'review-1', 'tasks': [
            {'kind': 'image', 'title': '图', 'brief': '内容'}]}
        plan_id = self.plan(True)
        request_id = task_request_id(self.sid, f'plan:{plan_id}:produce', 'review-lab', 'review-1',
            production_tasks_payload(StartRunArgs.model_validate(self.arguments)))
        with self.db.session() as session:
            session.add(ProductionTask(id='bad-snapshot', topic_id='review-1', title='图', position=0,
                create_request_id=request_id, kind='image', definition_json={}, dependency_json=[], dispatch_state='dispatched'))
            session.flush()
            run = session.get(ContentRun, self.run.id)
            run.task_id, run.idempotency_key, run.input_snapshot_json = 'bad-snapshot', 'task:bad-snapshot', []
            session.add(WriteReceipt(request_id=request_id, operation='queue_topics', resource_id='review-1',
                origin='agent', response_json={'creator_id': 'review-lab', 'kind': 'production_tasks',
                                               'task_ids': ['bad-snapshot']}))
        with patch('creatoros.agent.plans.Thread'), patch('creatoros.agent.plans.ThreadPoolExecutor'):
            self.service.start()
            self.assertEqual(self.service.get(self.sid, plan_id)['steps'][0]['status'], 'unknown')

    def test_restart_without_matching_write_receipt_is_unknown_without_dispatch(self):
        self.arguments = {'action': 'tasks', 'topic_id': 'review-1', 'tasks': [
            {'kind': 'image', 'title': '图', 'brief': '内容'}]}
        plan_id = self.plan(True)
        with patch('creatoros.agent.plans.Thread'), patch('creatoros.agent.plans.ThreadPoolExecutor'), \
             patch('creatoros.agent.plans.execute_tool_call') as tool:
            self.service.start()
            self.assertEqual(self.service.get(self.sid, plan_id)['steps'][0]['status'], 'unknown')
            tool.assert_not_called()
        self.assertEqual(self.producer.calls, 0)

    def test_invalid_partial_receipt_never_becomes_success_after_siblings_finish(self):
        second = self.runs.create('review-2')
        revision = self.runs.get_active_revision(second.id)
        self.queue('needs_attention')
        with self.db.session() as session:
            session.add(ProductionQueueItem(run_id=second.id, revision_id=revision.id,
                expected_version=second.version, status='pending'))
        self.arguments = {'action': 'start', 'topic_ids': ['review-1', 'review-2']}
        plan_id = self.plan(True)
        receipt = {'accepted_count': 1, 'items': [
            {'topic_id': 'review-1', 'run_id': self.run.id, 'status': 'queued', 'accepted': True},
            {'topic_id': 'review-2', 'run_id': second.id, 'status': 'queued', 'accepted': True},
        ]}
        self.execute(plan_id, receipt)
        self.assertEqual(self.service.get(self.sid, plan_id)['steps'][0]['status'], 'unknown')
        with self.db.session() as session:
            # Even a stale waiting row must recheck the frozen original receipt.
            session.get(AgentPlanStep, plan_id + ':produce').status = 'waiting'
            for run, rev in ((self.run, self.revision), (second, revision)):
                session.get(ContentRun, run.id).status = ContentRunStatus.AWAITING_APPROVAL
                session.get(ContentRevision, rev.id).artifact_digest = 'c' * 64
                session.query(ProductionQueueItem).filter_by(run_id=run.id).one().status = 'succeeded'
        self.service.advance()
        plan = self.service.get(self.sid, plan_id)
        self.assertEqual(plan['steps'][0]['status'], 'unknown')
        self.assertEqual(plan['steps'][1]['status'], 'blocked')
        self.assertEqual(plan['steps'][0]['result']['_submission_receipt'], receipt)
        self.assertEqual(self.producer.calls, 0)
