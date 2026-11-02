"""Explicit local recovery of a completed video receipt with a wrong file path.

Inspection is read-only. Applying requires the observed Run version and exact
inspection fingerprint, takes the local execution lock, creates a new Attempt,
and never starts or resumes Codex. The original receipt is not rewritten.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select

from creatoros.delivery import validate_artifact
from creatoros.delivery.files import atomic_json, safe_file
from creatoros.delivery.task_contracts import TaskExecutionSpec
from creatoros.delivery.task_execution import file_sha256, freeze_execution_spec, verify_execution_spec
from creatoros.delivery.video import (SOURCE_EXECUTION_SPEC, SOURCE_WORKER_RECEIPT, SOURCE_WORKER_TASK,
    VIDEO_CHECKPOINT, VIDEO_CORRECTION,
    VIDEO_MANIFEST, VideoArtifact, VideoCheckpoint, VideoDelivery, VideoPathCorrection,
    _identity, completed_worker_receipt, normalize_video_work_path)
from creatoros.delivery.video_receipt import _delivery_source
from creatoros.integrations.codex import CodexUsage, ProductionSession, SESSION_FILENAME
from creatoros.integrations.media_tools import probe_media
from creatoros.integrations.producer_skills import ProducerSkillCatalog
from creatoros.storage import (ContentAttemptStatus, ContentRunStatus, Creator, Database,
                               ProductionTask, Series, Topic, TopicRemoval)
from creatoros.storage.models import ProductionQueueItem
from .models import ContentRunInput, RunExecutionResult
from .repository import ContentRunRepository
from .service import ContentRunError, ContentRunService


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _fingerprint(value: dict) -> str:
    return _sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8'))


def _assert_current_scope(session, service, run, revision):
    """Keep the frozen Run, current account, Topic and optional Task aligned."""
    service._assert_current_run(session, run)
    if revision is None or revision.content_run_id != run.id:
        raise ContentRunError('当前 Revision 不存在。', code='scope_conflict')
    data = ContentRunInput.model_validate(revision.production_input_json)
    topic = session.get(Topic, run.topic_id)
    series = session.get(Series, topic.series_id) if topic else None
    creator = session.get(Creator, series.creator_id) if series else None
    task = session.get(ProductionTask, run.task_id) if run.task_id else None
    if (topic is None or series is None or creator is None or not series.is_active or not creator.is_active
            or session.get(TopicRemoval, run.topic_id) is not None or data.creator_id != series.creator_id
            or data.series_id != topic.series_id or data.topic_id != topic.id or data.task_id != run.task_id
            or (run.task_id and (task is None or task.topic_id != topic.id or task.archived_at is not None
                or (task.definition_json or {}).get('creator_id') != series.creator_id
                or (task.definition_json or {}).get('series_id') != topic.series_id))):
        raise ContentRunError('任务归属已变化，不能恢复或重放交付。', code='scope_conflict')
    return data


def _source_evidence(source: Path, selected: str, expected_input: TaskExecutionSpec,
                     expected_payload: dict, thread_id: str) -> dict:
    checkpoint_path = safe_file(source, source / VIDEO_CHECKPOINT, 8_000_000)
    checkpoint_raw = checkpoint_path.read_bytes()
    checkpoint = VideoCheckpoint.model_validate_json(checkpoint_raw)
    receipt = safe_file(source, source / 'worker_receipt.json', 256_000)
    request = safe_file(source, source / 'production_request.txt', 2_000_000)
    spec_file = safe_file(source, source / 'execution_spec.json', 2_000_000)
    task_file = safe_file(source, source / 'worker_task.json', 256_000)
    payload = json.loads(request.read_text(encoding='utf-8'))
    spec = TaskExecutionSpec.model_validate_json(spec_file.read_text(encoding='utf-8'))
    task = json.loads(task_file.read_text(encoding='utf-8'))
    if (payload != expected_payload or spec.kind != 'video' or spec.public_dict() != expected_input.public_dict()
            or checkpoint.input_digest != _identity(spec, payload) or checkpoint.thread_id != thread_id
            or checkpoint.handoff_protocol != 'final-json-v1' or not checkpoint.turn_completed
            or checkpoint.accepted_sha256 or not completed_worker_receipt(receipt, thread_id)
            or task.get('protocol') != 'creatoros-worker-v1' or task.get('kind') != 'production'
            or task.get('input_ref') != 'production_request.txt' or task.get('deliverable') != VIDEO_MANIFEST
            or task.get('scope') != {key: payload[key] for key in ('pack_id', 'creator_id', 'series_id', 'topic_id')}):
        raise ValueError('原视频输入、归属或 SDK 完成凭证不匹配。')
    verify_execution_spec(spec, source, ready=True)
    delivery = VideoDelivery.model_validate_json(checkpoint.response or '')
    if not delivery.complete or not delivery.video_path or delivery.error is not None:
        raise ValueError('原回执未明确声明成功的视频路径，不能作为路径纠正。')
    try:
        _delivery_source(source, delivery)
    except (ValueError, OSError):
        pass
    else:
        raise ValueError('原视频回执已经有效，请使用普通离线恢复。')
    selected_file = safe_file(source / 'work', source / 'work' / selected)
    selected_hash = file_sha256(selected_file)
    # Decode the explicitly selected bytes, then ensure they did not change.
    candidate = delivery.model_copy(update={'video_path': selected})
    _delivery_source(source, candidate)
    if file_sha256(selected_file) != selected_hash:
        raise ValueError('检查期间选定的视频发生变化。')
    return dict(input_digest=checkpoint.input_digest, thread_id=thread_id,
        checkpoint_sha256=_sha256(checkpoint_raw), response_sha256=_sha256(checkpoint.response.encode('utf-8')),
        worker_receipt_sha256=file_sha256(receipt), request_sha256=file_sha256(request),
        execution_spec_sha256=file_sha256(spec_file), worker_task_sha256=file_sha256(task_file),
        selected_sha256=selected_hash)


def inspect_video_correction(service: ContentRunService, run_id: str, source_attempt_id: str,
                             selected_work_path: str) -> dict:
    """Inspect one failed Attempt and one explicitly named work-relative MP4."""
    selected = normalize_video_work_path(selected_work_path)
    with service.database.session() as session:
        repository = ContentRunRepository(service.database, session=session)
        run = service._require(repository, run_id)
        revision = repository.get_revision_number(run_id, run.active_revision_number)
        data = _assert_current_scope(session, service, run, revision)
        attempts = repository.list_attempts(revision.id) if revision else ()
        attempt = next((item for item in attempts if item.id == source_attempt_id), None)
        if (run.status is not ContentRunStatus.FAILED or run.error_type != 'video_delivery_failed'
                or not run.retryable or run.lease_owner or run.lease_expires_at
                or attempt is None or not attempts or attempt.id != attempts[-1].id
                or attempt.status is not ContentAttemptStatus.FAILED or attempt.error_type != 'video_delivery_failed'
                or not attempt.output_directory or not attempt.producer_thread_id
                or run.producer_thread_id != attempt.producer_thread_id
                or data.production_protocol != 'task-v1' or data.execution_spec is None
                or data.execution_spec.kind != 'video'):
            raise ContentRunError('仅可纠正当前账号下最新失败视频 Attempt 的已完成回执。', code='invalid_video_correction')
        root = service.output_root / data.creator_id / data.series_id / run_id
        source = root / f'revision-{revision.revision_number:03d}' / f'attempt-{attempt.attempt_number:03d}'
        if (Path(attempt.output_directory).absolute() != source.absolute()
                or any(part.is_symlink() or getattr(part, 'is_junction', lambda: False)()
                       for part in (source, *source.parents))):
            raise ValueError('原 Attempt 路径不属于本 Run 或经过链接。')
        payload = dict(pack_id=f'{run_id}-r{revision.revision_number:03d}', creator_id=data.creator_id,
            series_id=data.series_id, topic_id=data.topic_id, topic_title=data.topic_title,
            topic_brief=data.topic_brief, series_description=data.series_description,
            audience=data.audience, revision_instruction=revision.instruction)
        identity = dict(run_id=run_id, revision_id=revision.id, run_version=run.version,
                        source_attempt_id=attempt.id, source_attempt_number=attempt.attempt_number,
                        selected_work_path=selected)
        thread_id = attempt.producer_thread_id
        expected_input = data.execution_spec
    evidence = _source_evidence(source, selected, expected_input, payload, thread_id)
    frozen = {**identity, **evidence}
    checkpoint_path = safe_file(source, source / VIDEO_CHECKPOINT, 8_000_000)
    if file_sha256(checkpoint_path) != evidence['checkpoint_sha256']:
        raise ValueError('检查期间原回执发生变化。')
    checkpoint = VideoCheckpoint.model_validate_json(checkpoint_path.read_text(encoding='utf-8'))
    selected_file = safe_file(source / 'work', source / 'work' / selected)
    metadata = probe_media(selected_file)
    if file_sha256(selected_file) != evidence['selected_sha256']:
        raise ValueError('检查期间选定的视频发生变化。')
    return {**frozen, 'fingerprint': _fingerprint(frozen), 'source_directory': str(source),
            'original_delivery': VideoDelivery.model_validate_json(checkpoint.response).model_dump(mode='json'),
            'selected_video': {key: metadata[key] for key in
                ('duration_seconds', 'width', 'height', 'has_audio', 'video_codec', 'audio_codec')},
            'selection_source': 'explicit_local_selection'}


class _OfflineVideoProducer:
    def __init__(self, service: ContentRunService, preview: dict):
        self.service, self.preview = service, preview

    def record_commit_failure(self, prepared, owner_id, error, elapsed, *, stage):
        class CorrectionFailure(RuntimeError):
            error_type = 'offline_video_correction_failed'

        self.service._mark_failed(prepared['run_id'], prepared['attempt_id'], stage=stage,
            error=CorrectionFailure(f'离线纠正未完成：{error} 原 Attempt 与回执已保留；请核对栏目归属和纠正记录，不会自动生产。'),
            elapsed=elapsed, owner_id=owner_id)

    def guard_and_reconcile_commit(self, session, run, revision):
        """Recheck ownership at each commit; finish any failed queue receipt atomically."""
        _assert_current_scope(session, self.service, run, revision)
        if run.id != self.preview['run_id'] or revision.id != self.preview['revision_id']:
            raise ContentRunError('恢复期间任务归属已变化，不能接收交付。', code='scope_conflict')
        if run.status is ContentRunStatus.VALIDATING:
            item = session.scalar(select(ProductionQueueItem).where(
                ProductionQueueItem.run_id == run.id, ProductionQueueItem.revision_id == revision.id))
            if item is not None:
                if item.status not in {'failed', 'needs_attention'}:
                    raise ContentRunError('恢复期间队列状态已变化，不能接收交付。', code='queue_conflict')
                item.status, item.last_error = 'succeeded', None
                item.completed_at = datetime.now(timezone.utc)

    def produce_to(self, **request):
        selected = self.preview['selected_work_path']
        source = Path(self.preview['source_directory'])
        payload = {key: request.get(key) for key in ('pack_id', 'creator_id', 'series_id', 'topic_id', 'topic_title',
                    'topic_brief', 'series_description', 'audience', 'revision_instruction')}
        expected_input = TaskExecutionSpec.model_validate(request['execution_spec'])
        evidence = _source_evidence(source, selected, expected_input, payload, self.preview['thread_id'])
        if any(evidence[key] != self.preview[key] for key in evidence):
            raise ValueError('开始恢复后源回执或视频发生变化。')
        directory = Path(request['directory']).resolve()
        directory.mkdir(parents=True, exist_ok=False)
        (directory / 'work').mkdir()
        spec = freeze_execution_spec(expected_input, ProducerSkillCatalog(request['skills_root']), directory,
            trusted_skill_roots=[directory.parent.parent / 'skill-snapshot', directory.parent.parent / 'revision-inputs'])
        verify_execution_spec(spec, directory, ready=True)
        (directory / 'execution_spec.json').write_text(spec.model_dump_json(indent=2), encoding='utf-8')
        shutil.copyfile(source / 'production_request.txt', directory / 'production_request.txt')
        shutil.copyfile(source / VIDEO_CHECKPOINT, directory / VIDEO_CHECKPOINT)
        shutil.copyfile(source / 'worker_receipt.json', directory / SOURCE_WORKER_RECEIPT)
        shutil.copyfile(source / 'worker_task.json', directory / SOURCE_WORKER_TASK)
        shutil.copyfile(source / 'execution_spec.json', directory / SOURCE_EXECUTION_SPEC)
        copied_evidence = {
            'checkpoint_sha256': directory / VIDEO_CHECKPOINT,
            'worker_receipt_sha256': directory / SOURCE_WORKER_RECEIPT,
            'worker_task_sha256': directory / SOURCE_WORKER_TASK,
            'execution_spec_sha256': directory / SOURCE_EXECUTION_SPEC,
            'request_sha256': directory / 'production_request.txt',
        }
        if any(file_sha256(path) != self.preview[key] for key, path in copied_evidence.items()):
            raise ValueError('复制后的原始回执或任务输入摘要不一致。')
        selected_source = safe_file(source / 'work', source / 'work' / selected)
        selected_target = directory / 'work' / selected
        selected_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(selected_source, selected_target)
        verified_again = _source_evidence(source, selected, expected_input, payload, self.preview['thread_id'])
        if (file_sha256(selected_target) != self.preview['selected_sha256']
                or any(verified_again[key] != self.preview[key] for key in evidence)):
            raise ValueError('复制期间源回执或视频发生变化。')
        checkpoint = VideoCheckpoint.model_validate_json((directory / VIDEO_CHECKPOINT).read_text(encoding='utf-8'))
        delivery = VideoDelivery.model_validate_json(checkpoint.response)
        video = directory / 'video.mp4'
        shutil.copyfile(selected_target, video)
        if file_sha256(video) != self.preview['selected_sha256']:
            raise ValueError('复制后视频摘要不一致。')
        artifact = VideoArtifact(**{key: payload[key] for key in ('pack_id', 'creator_id', 'series_id', 'topic_id', 'topic_title')},
            title=delivery.title, text=delivery.text, generated_at=datetime.now().astimezone().isoformat(),
            video_path='video.mp4', sha256=self.preview['selected_sha256'])
        correction = VideoPathCorrection(source_run_id=self.preview['run_id'], source_revision_id=self.preview['revision_id'],
            source_run_version=self.preview['run_version'],
            source_attempt_id=self.preview['source_attempt_id'], source_attempt_number=self.preview['source_attempt_number'],
            pack_id=payload['pack_id'], thread_id=checkpoint.thread_id, input_digest=checkpoint.input_digest,
            source_checkpoint_sha256=self.preview['checkpoint_sha256'], source_response_sha256=self.preview['response_sha256'],
            source_worker_receipt_sha256=self.preview['worker_receipt_sha256'], selected_work_path=selected,
            source_request_sha256=self.preview['request_sha256'], source_execution_spec_sha256=self.preview['execution_spec_sha256'],
            source_worker_task_sha256=self.preview['worker_task_sha256'],
            selected_sha256=self.preview['selected_sha256'], inspection_fingerprint=self.preview['fingerprint'])
        atomic_json(directory / VIDEO_MANIFEST, artifact)
        atomic_json(directory / VIDEO_CORRECTION, correction)
        session = ProductionSession(thread_id=checkpoint.thread_id, pack_id=payload['pack_id'],
                                    created_at=artifact.generated_at, usage=CodexUsage())
        atomic_json(directory / SESSION_FILENAME, session)
        (directory / 'codex_trace.jsonl').write_text(json.dumps({'type': 'offline_video_path_correction',
            'source_attempt_id': correction.source_attempt_id, 'fingerprint': correction.inspection_fingerprint,
            'selected_sha256': correction.selected_sha256}, ensure_ascii=False) + '\n', encoding='utf-8')
        validate_artifact(directory, production_protocol='task-v1', execution_spec=expected_input)
        if request.get('on_thread_started'):
            request['on_thread_started'](checkpoint.thread_id)
        return SimpleNamespace(directory=directory, pack=artifact, session=session)


def _existing_correction(service, run_id, source_attempt_id, selected, fingerprint, expected_version):
    """Return an already validated identical correction without creating an Attempt."""
    with service.database.session() as session:
        repository = ContentRunRepository(service.database, session=session)
        run = service._require(repository, run_id)
        revision = repository.get_revision_number(run_id, run.active_revision_number)
        data = _assert_current_scope(session, service, run, revision)
        for attempt in repository.list_attempts(revision.id):
            if not attempt.output_directory:
                continue
            directory = Path(attempt.output_directory)
            expected = (service.output_root / data.creator_id / data.series_id / run_id
                        / f'revision-{revision.revision_number:03d}' / f'attempt-{attempt.attempt_number:03d}')
            if (directory.absolute() != expected.absolute()
                    or any(part.is_symlink() or getattr(part, 'is_junction', lambda: False)()
                           for part in (directory, *directory.parents))):
                raise ContentRunError('原 Attempt 路径不属于本 Run 或经过链接。', code='invalid_video_correction')
            marker = directory / VIDEO_CORRECTION
            if not marker.exists() and not marker.is_symlink():
                continue
            correction = VideoPathCorrection.model_validate_json(safe_file(directory, marker, 256_000).read_text(encoding='utf-8'))
            if (correction.source_run_id != run_id or correction.source_revision_id != revision.id
                    or correction.source_attempt_id != source_attempt_id or correction.selected_work_path != selected
                    or correction.inspection_fingerprint != fingerprint):
                continue
            if correction.source_run_version != expected_version:
                raise ContentRunError('Run version 与原检查结果不一致。', code='version_conflict')
            if (run.status not in {ContentRunStatus.AWAITING_APPROVAL, ContentRunStatus.APPROVED}
                    or attempt.status is not ContentAttemptStatus.SUCCEEDED
                    or revision.artifact_directory != str(directory) or not revision.artifact_digest):
                raise ContentRunError('此前恢复未完成，保留证据等待核查。')
            queued = session.scalar(select(ProductionQueueItem).where(
                ProductionQueueItem.run_id == run_id, ProductionQueueItem.revision_id == revision.id))
            if queued is not None and queued.status != 'succeeded':
                raise ContentRunError('已恢复结果与队列状态不一致，请先核对。', code='queue_conflict')
            validation = validate_artifact(directory, production_protocol='task-v1', execution_spec=data.execution_spec)
            if validation.artifact_digest != revision.artifact_digest:
                raise ContentRunError('已恢复视频发生变化。', code='artifact_changed')
            return RunExecutionResult(run_id=run_id, revision_id=revision.id, attempt_id=attempt.id,
                status=run.status.value, artifact_directory=str(directory), artifact_digest=revision.artifact_digest,
                producer_thread_id=attempt.producer_thread_id)
    return None


def apply_video_correction(service: ContentRunService, run_id: str, source_attempt_id: str,
                           selected_work_path: str, *, expected_version: int, expected_fingerprint: str):
    """Finalize only the explicitly inspected bytes; caller never supplies an arbitrary source directory."""
    with service.guard:
        service.guard.assert_clean()
        with service._write_lock:
            selected = normalize_video_work_path(selected_work_path)
            existing = _existing_correction(service, run_id, source_attempt_id, selected, expected_fingerprint,
                                            expected_version)
            if existing is not None:
                return existing
            preview = inspect_video_correction(service, run_id, source_attempt_id, selected_work_path)
            if preview['run_version'] != expected_version:
                raise ContentRunError('Run version 已变化，请重新检查。', code='version_conflict')
            if preview['fingerprint'] != expected_fingerprint:
                raise ContentRunError('原回执或所选视频已变化，请重新检查。', code='artifact_changed')
            with service.database.session() as session:
                if session.scalar(select(ProductionQueueItem.id).where(
                        ProductionQueueItem.status.in_(['pending', 'running'])).limit(1)):
                    raise ContentRunError('仍有任务等待或正在执行，暂不能离线恢复。')
                queued = session.scalar(select(ProductionQueueItem).where(
                    ProductionQueueItem.run_id == run_id,
                    ProductionQueueItem.revision_id == preview['revision_id']))
                if queued is not None and queued.status not in {'failed', 'needs_attention'}:
                    raise ContentRunError('当前队列结果不允许离线恢复。', code='queue_conflict')
            owner = f'video-correction:{uuid4()}'
            prepared = service.claim(run_id, owner_id=owner, expected_version=expected_version)
        return service.execute_claimed(prepared, owner_id=owner,
                                       producer_override=_OfflineVideoProducer(service, preview))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--source-attempt-id', required=True)
    parser.add_argument('--work-relative-mp4', required=True)
    parser.add_argument('--database-url')
    parser.add_argument('--output-root', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected-version', type=int)
    parser.add_argument('--fingerprint')
    args = parser.parse_args(argv)
    if args.apply and (args.expected_version is None or not args.fingerprint):
        parser.error('--apply requires --expected-version and --fingerprint from inspection')
    database = Database(args.database_url) if args.database_url else Database.from_defaults()
    try:
        service = ContentRunService(database, output_root=args.output_root)
        result = (apply_video_correction(service, args.run_id, args.source_attempt_id, args.work_relative_mp4,
                    expected_version=args.expected_version, expected_fingerprint=args.fingerprint).model_dump(mode='json')
                  if args.apply else inspect_video_correction(service, args.run_id, args.source_attempt_id,
                                                              args.work_relative_mp4))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        database.close()


if __name__ == '__main__':
    main()
