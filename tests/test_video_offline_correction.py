"""Explicit correction of a completed video receipt; no model or producer calls."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
from threading import Event, Thread
import unittest
from unittest.mock import patch

from sqlalchemy import select

from creatoros.delivery.task_contracts import TaskExecutionSpec, TaskSkill
from creatoros.delivery.video import (SOURCE_EXECUTION_SPEC, SOURCE_WORKER_RECEIPT, SOURCE_WORKER_TASK,
                                      VIDEO_CORRECTION)
from creatoros.integrations.producer_skills import ProducerSkillCatalog, _digest, skills_root_for
from creatoros.integrations.codex import CodexProducerError
from creatoros.integrations.video_production import VIDEO_CHECKPOINT, VideoDelivery
from creatoros.integrations.visual_production import atomic_json
from creatoros.integrations.worker_protocol import record_thread, record_turn
from creatoros.runs import ContentRunError, ContentRunExecutionError, ContentRunService
from creatoros.storage import (ContentRun, ContentRunStatus, Creator, CreatorPlatform, Database, Series,
                               Topic, TopicSource, upgrade_database)
from creatoros.storage.models import ProductionQueueItem
from tests import test_task_video_execution as fixtures


def tree_bytes(root):
    return {item.relative_to(root).as_posix(): item.read_bytes()
            for item in root.rglob('*') if item.is_file()}


class OfflineVideoCorrectionTests(unittest.TestCase):
    def setUp(self):
        fixtures.TaskVideoExecutionTests.setUpClass()
        self.fixture = fixtures.TaskVideoExecutionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        root = self.fixture.root
        url = 'sqlite:///' + (root / 'offline-video.db').as_posix()
        upgrade_database(url)
        self.database = Database(url)
        self.addCleanup(self.database.close)
        catalog = ProducerSkillCatalog(skills_root_for(self.database), project_root=root)
        record = catalog.register_local(root / 'source-skill', role='production')
        spec = self.fixture.spec.model_copy(update={
            'skills': [TaskSkill(skill_id=record['id'], digest=_digest(Path(record['local_path'])), role='primary')],
            'materials': [item.model_copy(update={'path': str(root / ('images.zip' if item.kind == 'images_zip' else 'source.wav'))})
                          for item in self.fixture.spec.materials],
        })
        self.spec = spec
        with self.database.session() as session:
            session.add(Creator(id='creator', display_name='Test', platform=CreatorPlatform.XIAOHONGSHU))
            session.add(Series(id='series', creator_id='creator', name='Series', description='', audience='',
                               skill_name='knowledge-to-carousel'))
            session.add(Topic(id='topic', series_id='series', title='Title', brief='brief',
                              source=TopicSource.MANUAL, position=1))
        self.service = ContentRunService(self.database, producer_factory=lambda: self.fixture.producer,
                                         output_root=root / 'outputs')
        self.run = self.service._create('topic', execution_spec=spec)

        async def completed_with_missing_path(*args):
            checkpoint = await self.fixture.completed_transport(*args)
            checkpoint.response = VideoDelivery(title='Title', text='Caption', video_path='missing.mp4',
                                                complete=True).model_dump_json()
            atomic_json(args[1] / VIDEO_CHECKPOINT, checkpoint)
            record_thread(args[1], checkpoint.thread_id)
            record_turn(args[1], 'completed-turn', 'production', 'completed')
            args[-1]['on_thread_started'](checkpoint.thread_id)
            return checkpoint

        with patch('creatoros.integrations.video_production._execute_video', completed_with_missing_path):
            with self.assertRaises(ContentRunExecutionError):
                self.service.execute(self.run.id)
        self.failed = self.service.get(self.run.id)
        self.revision = self.service.get_active_revision(self.run.id)
        self.old_attempt = self.service.repository.list_attempts(self.revision.id)[0]
        self.old_dir = Path(self.old_attempt.output_directory)
        self.old_files = tree_bytes(self.old_dir)

    def test_explicit_file_correction_finishes_same_revision_without_sdk(self):
        from creatoros.delivery import validate_artifact
        from creatoros.runs.video_correction import inspect_video_correction, apply_video_correction

        preview = inspect_video_correction(self.service, self.run.id, self.old_attempt.id, 'finished.mp4')
        self.assertEqual(preview['run_version'], self.failed.version)
        self.assertEqual(preview['original_delivery']['video_path'], 'missing.mp4')
        self.assertEqual(preview['selection_source'], 'explicit_local_selection')
        self.assertTrue(preview['selected_video']['has_audio'])
        with patch.object(self.service, 'producer_factory', side_effect=AssertionError('producer must not be called')):
            with patch('creatoros.integrations.video_production._execute_video',
                       side_effect=AssertionError('SDK must not be called')):
                result = apply_video_correction(self.service, self.run.id, self.old_attempt.id, 'finished.mp4',
                    expected_version=preview['run_version'], expected_fingerprint=preview['fingerprint'])
        self.assertEqual(result.status, ContentRunStatus.AWAITING_APPROVAL.value)
        self.assertEqual(result.revision_id, self.revision.id)
        self.assertNotEqual(result.attempt_id, self.old_attempt.id)
        self.assertEqual(tree_bytes(self.old_dir), self.old_files)
        target = Path(result.artifact_directory)
        self.assertEqual((target / 'video.mp4').read_bytes(), self.fixture.clip.read_bytes())
        old_response = json.loads((self.old_dir / VIDEO_CHECKPOINT).read_text(encoding='utf-8'))['response']
        new_response = json.loads((target / VIDEO_CHECKPOINT).read_text(encoding='utf-8'))['response']
        self.assertEqual(new_response, old_response)
        self.assertEqual(json.loads(new_response)['video_path'], 'missing.mp4')
        checked = validate_artifact(target, production_protocol='task-v1',
                                    execution_spec=self.revision.production_input_json['execution_spec'])
        self.assertEqual(checked.artifact_digest, result.artifact_digest)
        self.assertTrue(checked.video.has_audio)

    def test_identical_correction_replays_without_another_attempt(self):
        from creatoros.runs.video_correction import inspect_video_correction, apply_video_correction

        preview = inspect_video_correction(self.service, self.run.id, self.old_attempt.id, 'finished.mp4')
        args = (self.service, self.run.id, self.old_attempt.id, 'finished.mp4')
        options = dict(expected_version=preview['run_version'], expected_fingerprint=preview['fingerprint'])
        with patch.object(self.service, 'producer_factory', side_effect=AssertionError('no producer')):
            first = apply_video_correction(*args, **options)
            with self.assertRaises(ContentRunError) as error:
                apply_video_correction(*args, expected_version=preview['run_version'] + 1,
                                       expected_fingerprint=preview['fingerprint'])
            self.assertEqual(error.exception.code, 'version_conflict')
            second = apply_video_correction(*args, **options)
        self.assertEqual(first.attempt_id, second.attempt_id)
        self.assertEqual(len(self.service.repository.list_attempts(self.revision.id)), 2)
        self.assertEqual(tree_bytes(self.old_dir), self.old_files)

    def test_owned_production_task_can_be_corrected(self):
        from creatoros.runs.video_correction import inspect_video_correction, apply_video_correction
        from creatoros.storage import ProductionTask

        with self.database.session() as session:
            session.add(ProductionTask(id='task', topic_id='topic', title='Video task', position=0,
                create_request_id='task-request', kind='video',
                definition_json={'creator_id': 'creator', 'series_id': 'series'}))
        run = self.service._create('topic', task_id='task', execution_spec=self.spec)

        async def completed_with_missing_path(*args):
            checkpoint = await self.fixture.completed_transport(*args)
            checkpoint.response = VideoDelivery(title='Task video', text='Caption', video_path='missing.mp4',
                                                complete=True).model_dump_json()
            atomic_json(args[1] / VIDEO_CHECKPOINT, checkpoint)
            record_thread(args[1], checkpoint.thread_id)
            record_turn(args[1], 'task-turn', 'production', 'completed')
            args[-1]['on_thread_started'](checkpoint.thread_id)
            return checkpoint

        with patch('creatoros.integrations.video_production._execute_video', completed_with_missing_path):
            with self.assertRaises(ContentRunExecutionError):
                self.service.execute(run.id)
        revision = self.service.get_active_revision(run.id)
        attempt = self.service.repository.list_attempts(revision.id)[0]
        preview = inspect_video_correction(self.service, run.id, attempt.id, 'finished.mp4')
        with patch.object(self.service, 'producer_factory', side_effect=AssertionError('no producer')):
            result = apply_video_correction(self.service, run.id, attempt.id, 'finished.mp4',
                expected_version=preview['run_version'], expected_fingerprint=preview['fingerprint'])
        self.assertEqual(result.status, ContentRunStatus.AWAITING_APPROVAL.value)
        self.assertEqual(self.service.get_active_revision(run.id).production_input_json['task_id'], 'task')

    def test_no_implicit_choice_or_path_escape(self):
        from creatoros.runs.video_correction import inspect_video_correction

        for selected in ('', '../outside.mp4', str(self.fixture.clip), 'work/../../outside.mp4'):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                inspect_video_correction(self.service, self.run.id, self.old_attempt.id, selected)
        self.assertEqual(self.service.get(self.run.id).status, ContentRunStatus.FAILED)
        self.assertEqual(len(self.service.repository.list_attempts(self.revision.id)), 1)
        self.assertEqual(tree_bytes(self.old_dir), self.old_files)

    def test_changed_receipt_after_inspection_is_rejected_before_claim(self):
        from creatoros.runs.video_correction import inspect_video_correction, apply_video_correction

        preview = inspect_video_correction(self.service, self.run.id, self.old_attempt.id, 'finished.mp4')
        checkpoint_path = self.old_dir / VIDEO_CHECKPOINT
        checkpoint = json.loads(checkpoint_path.read_text(encoding='utf-8'))
        checkpoint['response'] = checkpoint['response'].replace('missing.mp4', 'different-missing.mp4')
        checkpoint_path.write_text(json.dumps(checkpoint), encoding='utf-8')
        with patch.object(self.service, 'producer_factory', side_effect=AssertionError('no producer')):
            with self.assertRaises(ContentRunError) as error:
                apply_video_correction(self.service, self.run.id, self.old_attempt.id, 'finished.mp4',
                    expected_version=preview['run_version'], expected_fingerprint=preview['fingerprint'])
        self.assertEqual(error.exception.code, 'artifact_changed')
        self.assertEqual(len(self.service.repository.list_attempts(self.revision.id)), 1)
        self.assertEqual(self.service.get(self.run.id).status, ContentRunStatus.FAILED)

    def test_wrong_owner_or_missing_completed_turn_is_rejected(self):
        from creatoros.runs.video_correction import inspect_video_correction

        receipt = self.old_dir / 'worker_receipt.json'
        value = json.loads(receipt.read_text(encoding='utf-8'))
        value['turns'][-1]['status'] = 'interrupted'
        receipt.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaises(ValueError):
            inspect_video_correction(self.service, self.run.id, self.old_attempt.id, 'finished.mp4')
        receipt.write_bytes(self.old_files['worker_receipt.json'])
        with self.database.session() as session:
            session.add(Creator(id='other', display_name='Other', platform=CreatorPlatform.XIAOHONGSHU))
            session.get(Series, 'series').creator_id = 'other'
        with self.assertRaises(ContentRunError):
            inspect_video_correction(self.service, self.run.id, self.old_attempt.id, 'finished.mp4')
        self.assertEqual(len(self.service.repository.list_attempts(self.revision.id)), 1)

    def test_frozen_task_identity_must_match_run(self):
        from creatoros.runs.video_correction import inspect_video_correction

        from creatoros.storage import ContentRevision
        with self.database.session() as session:
            revision = session.get(ContentRevision, self.revision.id)
            revision.production_input_json = {**revision.production_input_json, 'task_id': 'other-task'}
        with self.assertRaises(ContentRunError):
            inspect_video_correction(self.service, self.run.id, self.old_attempt.id, 'finished.mp4')
        self.assertEqual(len(self.service.repository.list_attempts(self.revision.id)), 1)

    def test_contradictory_completed_error_cannot_be_corrected(self):
        from creatoros.runs.video_correction import inspect_video_correction

        path = self.old_dir / VIDEO_CHECKPOINT
        checkpoint = json.loads(path.read_text(encoding='utf-8'))
        delivery = json.loads(checkpoint['response'])
        delivery['error'] = 'render failed'
        checkpoint['response'] = json.dumps(delivery)
        path.write_text(json.dumps(checkpoint), encoding='utf-8')
        with self.assertRaises(ValueError):
            inspect_video_correction(self.service, self.run.id, self.old_attempt.id, 'finished.mp4')
        self.assertEqual(len(self.service.repository.list_attempts(self.revision.id)), 1)

    def test_unconfirmed_tool_delivery_cannot_be_promoted_by_manual_path(self):
        from creatoros.runs.video_correction import inspect_video_correction

        path = self.old_dir / VIDEO_CHECKPOINT
        original = json.loads(path.read_text(encoding='utf-8'))
        original['handoff_protocol'] = 'submit-tool-v1'
        path.write_text(json.dumps(original), encoding='utf-8')
        with self.assertRaises(ValueError):
            inspect_video_correction(self.service, self.run.id, self.old_attempt.id, 'finished.mp4')
        self.assertEqual(self.service.get(self.run.id).status, ContentRunStatus.FAILED)
        self.assertEqual(len(self.service.repository.list_attempts(self.revision.id)), 1)

    def test_changed_selected_video_after_inspection_is_rejected_before_claim(self):
        from creatoros.runs.video_correction import inspect_video_correction, apply_video_correction

        preview = inspect_video_correction(self.service, self.run.id, self.old_attempt.id, 'finished.mp4')
        selected = self.old_dir / 'work/finished.mp4'
        selected.write_bytes(selected.read_bytes() + b'\0')
        with patch.object(self.service, 'producer_factory', side_effect=AssertionError('no producer')):
            with self.assertRaises(ContentRunError) as error:
                apply_video_correction(self.service, self.run.id, self.old_attempt.id, 'finished.mp4',
                    expected_version=preview['run_version'], expected_fingerprint=preview['fingerprint'])
        self.assertEqual(error.exception.code, 'artifact_changed')
        self.assertEqual(self.service.get(self.run.id).status, ContentRunStatus.FAILED)
        self.assertEqual(len(self.service.repository.list_attempts(self.revision.id)), 1)

    def test_corrected_artifact_rejects_evidence_tampering(self):
        from creatoros.delivery import validate_artifact
        from creatoros.runs.video_correction import inspect_video_correction, apply_video_correction

        preview = inspect_video_correction(self.service, self.run.id, self.old_attempt.id, 'finished.mp4')
        result = apply_video_correction(self.service, self.run.id, self.old_attempt.id, 'finished.mp4',
            expected_version=preview['run_version'], expected_fingerprint=preview['fingerprint'])
        root = Path(result.artifact_directory)
        files = [VIDEO_CORRECTION, VIDEO_CHECKPOINT, SOURCE_WORKER_RECEIPT, SOURCE_WORKER_TASK,
                 SOURCE_EXECUTION_SPEC, 'production_request.txt', 'work/finished.mp4', 'video.mp4']
        for name in files:
            path = root / name
            original = path.read_bytes()
            path.write_bytes(original + b'\0')
            try:
                with self.subTest(name=name), self.assertRaises((ValueError, OSError)):
                    validate_artifact(root, production_protocol='task-v1',
                                      execution_spec=self.revision.production_input_json['execution_spec'])
            finally:
                path.write_bytes(original)
        checked = validate_artifact(root, production_protocol='task-v1',
                                    execution_spec=self.revision.production_input_json['execution_spec'])
        self.assertEqual(checked.artifact_digest, result.artifact_digest)

    def test_changed_version_or_cancelled_run_is_never_corrected(self):
        from creatoros.runs.video_correction import inspect_video_correction, apply_video_correction

        preview = inspect_video_correction(self.service, self.run.id, self.old_attempt.id, 'finished.mp4')
        with self.database.session() as session:
            row = session.get(ContentRun, self.run.id)
            row.version += 1
        with self.assertRaises(ContentRunError) as error:
            apply_video_correction(self.service, self.run.id, self.old_attempt.id, 'finished.mp4',
                expected_version=preview['run_version'], expected_fingerprint=preview['fingerprint'])
        self.assertEqual(error.exception.code, 'version_conflict')
        with self.database.session() as session:
            session.get(ContentRun, self.run.id).status = ContentRunStatus.CANCELLED
        with self.assertRaises(ContentRunError):
            inspect_video_correction(self.service, self.run.id, self.old_attempt.id, 'finished.mp4')
        self.assertEqual(len(self.service.repository.list_attempts(self.revision.id)), 1)

    def test_pending_queue_blocks_apply_without_mutation(self):
        from creatoros.runs.video_correction import inspect_video_correction, apply_video_correction

        preview = inspect_video_correction(self.service, self.run.id, self.old_attempt.id, 'finished.mp4')
        with self.database.session() as session:
            session.add(ProductionQueueItem(run_id=self.run.id, revision_id=self.revision.id,
                expected_version=preview['run_version'], status='pending'))
        with self.assertRaises(ContentRunError):
            apply_video_correction(self.service, self.run.id, self.old_attempt.id, 'finished.mp4',
                expected_version=preview['run_version'], expected_fingerprint=preview['fingerprint'])
        self.assertEqual(self.service.get(self.run.id).status, ContentRunStatus.FAILED)
        self.assertEqual(len(self.service.repository.list_attempts(self.revision.id)), 1)
        self.assertEqual(tree_bytes(self.old_dir), self.old_files)

    def test_failed_queue_receipt_tracks_successful_offline_correction(self):
        from creatoros.runs.video_correction import inspect_video_correction, apply_video_correction

        preview = inspect_video_correction(self.service, self.run.id, self.old_attempt.id, 'finished.mp4')
        with self.database.session() as session:
            session.add(ProductionQueueItem(run_id=self.run.id, revision_id=self.revision.id,
                expected_version=preview['run_version'], status='failed', last_error='original failure'))
        result = apply_video_correction(self.service, self.run.id, self.old_attempt.id, 'finished.mp4',
            expected_version=preview['run_version'], expected_fingerprint=preview['fingerprint'])
        self.assertEqual(result.status, ContentRunStatus.AWAITING_APPROVAL.value)
        with self.database.session() as session:
            item = session.scalar(select(ProductionQueueItem).where(ProductionQueueItem.run_id == self.run.id))
            self.assertEqual(item.status, 'succeeded')
            self.assertIsNotNone(item.completed_at)
            self.assertIsNone(item.last_error)

    def test_cancelled_queue_receipt_is_not_overridden(self):
        from creatoros.runs.video_correction import inspect_video_correction, apply_video_correction

        preview = inspect_video_correction(self.service, self.run.id, self.old_attempt.id, 'finished.mp4')
        with self.database.session() as session:
            session.add(ProductionQueueItem(run_id=self.run.id, revision_id=self.revision.id,
                expected_version=preview['run_version'], status='cancelled'))
        with self.assertRaises(ContentRunError) as error:
            apply_video_correction(self.service, self.run.id, self.old_attempt.id, 'finished.mp4',
                expected_version=preview['run_version'], expected_fingerprint=preview['fingerprint'])
        self.assertEqual(error.exception.code, 'queue_conflict')
        self.assertEqual(self.service.get(self.run.id).status, ContentRunStatus.FAILED)
        self.assertEqual(len(self.service.repository.list_attempts(self.revision.id)), 1)

    def test_transfer_during_file_copy_cannot_finalize_for_new_owner(self):
        from creatoros.runs import video_correction

        preview = video_correction.inspect_video_correction(self.service, self.run.id, self.old_attempt.id, 'finished.mp4')
        with self.database.session() as session:
            session.add(Creator(id='other', display_name='Other', platform=CreatorPlatform.XIAOHONGSHU))
        reached, proceed = Event(), Event()
        original_validate = video_correction.validate_artifact
        outcomes = []

        def pause_after_file_validation(*args, **kwargs):
            result = original_validate(*args, **kwargs)
            reached.set()
            if not proceed.wait(15):
                raise AssertionError('transfer fixture timed out')
            return result

        def apply():
            try:
                outcomes.append(video_correction.apply_video_correction(
                    self.service, self.run.id, self.old_attempt.id, 'finished.mp4',
                    expected_version=preview['run_version'], expected_fingerprint=preview['fingerprint']))
            except Exception as error:
                outcomes.append(error)

        with patch.object(video_correction, 'validate_artifact', pause_after_file_validation):
            worker = Thread(target=apply)
            worker.start()
            try:
                self.assertTrue(reached.wait(30))
                with self.database.session() as session:
                    session.get(Series, 'series').creator_id = 'other'
            finally:
                proceed.set()
                worker.join(timeout=30)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(outcomes), 1)
        self.assertIsInstance(outcomes[0], ContentRunError)
        self.assertNotEqual(self.service.get(self.run.id).status, ContentRunStatus.AWAITING_APPROVAL)
        self.assertEqual(tree_bytes(self.old_dir), self.old_files)

    def test_transfer_before_validation_does_not_succeed_or_finish_queue(self):
        from creatoros.runs.video_correction import inspect_video_correction, apply_video_correction

        preview = inspect_video_correction(self.service, self.run.id, self.old_attempt.id, 'finished.mp4')
        with self.database.session() as session:
            session.add(Creator(id='other', display_name='Other', platform=CreatorPlatform.XIAOHONGSHU))
            session.add(ProductionQueueItem(run_id=self.run.id, revision_id=self.revision.id,
                expected_version=preview['run_version'], status='failed', last_error='original failure'))
        original_validate = self.service._validate_active

        def transfer_before_validation(*args, **kwargs):
            with self.database.session() as session:
                session.get(Series, 'series').creator_id = 'other'
            return original_validate(*args, **kwargs)

        with patch.object(self.service, '_validate_active', side_effect=transfer_before_validation):
            with self.assertRaises(ContentRunExecutionError):
                apply_video_correction(self.service, self.run.id, self.old_attempt.id, 'finished.mp4',
                    expected_version=preview['run_version'], expected_fingerprint=preview['fingerprint'])
        self.assertEqual(self.service.get(self.run.id).status, ContentRunStatus.FAILED)
        with self.database.session() as session:
            item = session.scalar(select(ProductionQueueItem).where(ProductionQueueItem.run_id == self.run.id))
            self.assertEqual(item.status, 'failed')
            self.assertEqual(item.last_error, 'original failure')
        self.assertEqual(tree_bytes(self.old_dir), self.old_files)

    def test_disk_failure_preserves_source_and_does_not_claim_success(self):
        from creatoros.runs.video_correction import inspect_video_correction, apply_video_correction

        preview = inspect_video_correction(self.service, self.run.id, self.old_attempt.id, 'finished.mp4')
        original_copy = shutil.copyfile

        def fail_video_copy(source, target, *args, **kwargs):
            if Path(target).name == 'finished.mp4':
                raise OSError('injected disk failure')
            return original_copy(source, target, *args, **kwargs)

        with patch('creatoros.runs.video_correction.shutil.copyfile', side_effect=fail_video_copy):
            with patch.object(self.service, 'producer_factory', side_effect=AssertionError('no producer')):
                with self.assertRaises(ContentRunExecutionError):
                    apply_video_correction(self.service, self.run.id, self.old_attempt.id, 'finished.mp4',
                        expected_version=preview['run_version'], expected_fingerprint=preview['fingerprint'])
        self.assertEqual(self.service.get(self.run.id).status, ContentRunStatus.FAILED)
        attempts = self.service.repository.list_attempts(self.revision.id)
        self.assertEqual(len(attempts), 2)
        self.assertFalse(attempts[-1].retryable)
        self.assertEqual(tree_bytes(self.old_dir), self.old_files)

    def test_unconfirmed_completed_turn_is_not_retryable_in_run_service(self):
        root = self.fixture.root
        saved = TaskExecutionSpec.model_validate(self.revision.production_input_json['execution_spec'])
        spec = saved.model_copy(update={
            'skills': [item.model_copy(update={'path': None}) for item in saved.skills],
            'materials': [item.model_copy(update={'path': str(root / ('images.zip' if item.kind == 'images_zip' else 'source.wav'))})
                          for item in saved.materials],
        })
        with self.database.session() as session:
            session.add(Topic(id='unconfirmed-topic', series_id='series', title='Unconfirmed',
                              source=TopicSource.MANUAL, position=2))
        run = self.service._create('unconfirmed-topic', execution_spec=spec)

        async def unconfirmed(*args):
            raise CodexProducerError('completed without accepted receipt', error_type='video_delivery_unconfirmed')

        with patch('creatoros.integrations.video_production._execute_video', unconfirmed):
            with self.assertRaises(ContentRunExecutionError):
                self.service.execute(run.id)
        failed = self.service.get(run.id)
        self.assertEqual(failed.error_type, 'video_delivery_unconfirmed')
        self.assertFalse(failed.retryable)
        with patch.object(self.service, 'producer_factory', side_effect=AssertionError('must not run')):
            with self.assertRaises(ContentRunError):
                self.service.execute(run.id)


if __name__ == '__main__':
    unittest.main()
