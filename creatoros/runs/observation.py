"""Read production progress for orchestration without exposing ORM entities.

The caller provides its active database session: observing and persisting a plan
step therefore happen in the same transaction. This module never dispatches,
retries or modifies a production task.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from sqlalchemy import select

from creatoros.storage.models import (ContentRevision, ContentRun, ContentRunEvent, ContentRunEventType,
                                      ProductionQueueItem, ProductionTask, Series, Topic, WriteReceipt)
from creatoros.integrations.production_identity import reference_topic_id


@dataclass(frozen=True)
class ProductionObservation:
    # None means at least one sibling is still waiting. A tuple is a terminal
    # plan-step result, not a claim that an individual producer was restarted.
    terminal: tuple[str, str | None] | None
    # None means validation failed before an updated receipt could be produced.
    result: dict | None


def run_topic_id(session, run_id: str | None) -> str | None:
    """Resolve a Run to its topic without passing its storage object outward."""
    run = session.get(ContentRun, run_id) if isinstance(run_id, str) else None
    return run.topic_id if run else None


def only_revision_matches(session, run_id: str, revision_id: str, digest: str) -> bool:
    """A collection can use a plan's output only while its recorded revision is exact."""
    revisions = list(session.scalars(select(ContentRevision).where(ContentRevision.content_run_id == run_id)))
    return len(revisions) == 1 and revisions[0].id == revision_id and revisions[0].artifact_digest == digest


def _message(value, default):
    return value if isinstance(value, str) and value else default


def observe_production(session, creator_id: str, arguments: dict, receipt: dict) -> ProductionObservation:
    """Observe all submitted siblings and return their precise current evidence.

    A task may be waiting on a dependency before it has a Run. Failed siblings
    are retained while other siblings finish; finished output is pinned to its
    revision and digest. The caller owns persistence of the returned projection.
    """
    data = deepcopy(receipt or {})
    action = arguments.get('action')
    items = data.get('items', []) if action in {'start', 'tasks'} else [data]
    if not items or not isinstance(items, list):
        return ProductionObservation(('failed', '未获得可等待的制作任务。'), None)

    pending, failed, unknown = False, [], []
    for item in items:
        if not isinstance(item, dict):
            return ProductionObservation(('failed', '制作回执格式无效。'), None)
        identity, task = item.get('id'), None
        expected_run = item.get('run_id')
        if expected_run is not None and (not isinstance(expected_run, str) or not expected_run):
            return ProductionObservation(('failed', '制作回执中的任务标识无效。'), None)
        if action == 'tasks' and (not isinstance(identity, str) or not identity):
            return ProductionObservation(('failed', '制作回执中的制作项标识无效。'), None)
        run = session.get(ContentRun, expected_run) if expected_run else None
        if action == 'tasks':
            task = session.get(ProductionTask, identity) if identity else None
            if (not task or (expected_run and not run)
                    or (run and (run.task_id != identity or run.topic_id != task.topic_id))):
                return ProductionObservation(('failed', '制作任务与产物不匹配。'), None)
            if not run:
                run = session.scalar(select(ContentRun).where(ContentRun.idempotency_key == f'task:{identity}'))
                if not run and session.scalar(select(ContentRun.id).where(ContentRun.task_id == identity).limit(1)):
                    return ProductionObservation(('unknown', '无法确认制作项的原始任务，请核对。'), None)
            if run and (run.idempotency_key != f'task:{identity}' or run.task_id != identity):
                return ProductionObservation(('unknown', '制作项与原始任务不匹配，请核对。'), None)
            if run and not expected_run:
                item['run_id'] = run.id
        topic = session.get(Topic, run.topic_id if run else task.topic_id) if run or task else None
        series = session.get(Series, topic.series_id) if topic else None
        if (run or task) and (not series or series.creator_id != creator_id):
            return ProductionObservation(('failed', '制作任务不存在或账号范围不符。'), None)
        if (action == 'start' and run and run.topic_id != item.get('topic_id')) or (
                action == 'tasks' and isinstance(arguments.get('topic_id'), str)
                and task.topic_id != arguments['topic_id']):
            return ProductionObservation(('failed', '制作任务与选题不匹配。'), None)
        if item.get('status') == 'rejected' or item.get('error_code'):
            item['terminal_status'] = 'unknown'
            item['error'] = _message(item.get('error'), _message(item.get('message'), '制作回执需要核对。'))
            unknown.append(item['error'])
            continue
        task_state = getattr(task, 'dispatch_state', None)
        if task and run and run.status.value not in {'awaiting_approval', 'approved', 'published'} and task_state in {'needs_attention', 'cancelled'}:
            item['terminal_status'] = 'unknown' if task_state == 'needs_attention' else 'cancelled'
            item['error'] = _message(getattr(task, 'last_error', None), '制作任务需要核对。')
            (unknown if task_state == 'needs_attention' else failed).append(item['error'])
            continue
        if not run:
            if task and task.dispatch_state not in {'needs_attention', 'cancelled'}:
                pending = True
                item.pop('terminal_status', None)
                item.pop('error', None)
            else:
                state = task.dispatch_state if task else 'failed'
                message = task.last_error if task else item.get('message')
                item['terminal_status'] = 'unknown' if state == 'needs_attention' else state
                item['error'] = _message(message, '未获得可等待的制作任务。')
                (unknown if state == 'needs_attention' else failed).append(item['error'])
            continue
        if run.status.value in {'failed', 'interrupted', 'cancelled'}:
            item['terminal_status'], item['error'] = run.status.value, _message(run.error_message, '制作任务未完成。')
            failed.append(item['error'])
            continue
        revision = session.scalar(select(ContentRevision).where(ContentRevision.content_run_id == run.id,
            ContentRevision.revision_number == run.active_revision_number))
        queue = session.scalar(select(ProductionQueueItem).where(
            ProductionQueueItem.run_id == run.id, ProductionQueueItem.revision_id == revision.id)) if revision else None
        item.pop('queue_status', None)  # Only the current database row is authoritative.
        queue_status = getattr(queue, 'status', None)
        if queue_status is not None:
            item['queue_status'] = queue_status
        if run.status.value not in {'awaiting_approval', 'approved', 'published'} and queue_status == 'needs_attention':
            item['terminal_status'] = 'unknown'
            item['error'] = _message(getattr(queue, 'last_error', None), '生产队列需要核对，不能自动重派。')
            unknown.append(item['error'])
            continue
        if run.status.value not in {'awaiting_approval', 'approved', 'published'} and queue_status in {'failed', 'cancelled', 'succeeded'}:
            item['terminal_status'] = 'unknown' if queue_status == 'succeeded' else 'failed'
            item['error'] = _message(getattr(queue, 'last_error', None), '队列状态与生产状态不一致，请核对原任务。')
            (unknown if queue_status == 'succeeded' else failed).append(item['error'])
            continue
        if run.status.value not in {'awaiting_approval', 'approved', 'published'}:
            pending = True
            item.pop('terminal_status', None)
            item.pop('error', None)
            continue
        if not revision or not revision.artifact_digest:
            item['terminal_status'], item['error'] = 'failed', '制作任务没有可核验产物。'
            failed.append(item['error'])
            continue
        if item.get('revision_id') and (item['revision_id'] != revision.id or item.get('artifact_digest') != revision.artifact_digest):
            item['terminal_status'], item['error'] = 'failed', '制作任务产物版本发生变化，请重新核对。'
            failed.append(item['error'])
            continue
        item['revision_id'], item['artifact_digest'] = revision.id, revision.artifact_digest
        item['terminal_status'] = 'succeeded'
        item.pop('error', None)

    if pending:
        return ProductionObservation(None, data)
    if unknown:
        return ProductionObservation(('unknown', f'{len(unknown)} 项制作结果需要核对；其余项目已保留。' + unknown[0]), data)
    if failed:
        return ProductionObservation(('failed', f'{len(failed)} 项制作未完成；其余已完成项目已保留。' + failed[0]), data)
    return ProductionObservation(('succeeded', None), data)


def valid_submission_shape(arguments: dict, receipt: dict) -> bool:
    action = arguments.get('action')
    if not isinstance(receipt, dict):
        return False
    items = receipt.get('items') if action in {'start', 'tasks'} else [receipt]
    if not isinstance(items, list) or not items or any(not isinstance(item, dict) for item in items):
        return False
    if action == 'start':
        requested = arguments.get('topic_ids')
        if (not isinstance(requested, list) or any(not isinstance(topic, str) for topic in requested)
                or len(items) != len(set(requested))
                or any(not isinstance(item.get('topic_id'), str) for item in items)
                or any(item.get('run_id') is not None and not isinstance(item['run_id'], str) for item in items)
                or {item.get('topic_id') for item in items} != set(requested)
                or any(type(item.get('accepted')) is not bool for item in items)
                or type(receipt.get('accepted_count')) is not int
                or receipt.get('accepted_count') != sum(item['accepted'] for item in items)):
            return False
    elif action == 'tasks':
        requested = arguments.get('tasks')
        ids = [item.get('id') for item in items]
        if (not isinstance(requested, list) or len(items) != len(requested)
                or any(not isinstance(value, str) or not value for value in ids)
                or any(item.get('run_id') is not None and not isinstance(item['run_id'], str) for item in items)
                or len(set(ids)) != len(ids)):
            return False
    elif action in {'fresh', 'reference'}:
        if not isinstance(receipt.get('run_id'), str) or not receipt['run_id']:
            return False
    else:
        return False
    return True


def submission_shape_arguments(arguments: dict) -> dict:
    """Keep only the immutable fields needed to recheck a receipt later."""
    action = arguments.get('action')
    if action == 'start':
        return {'action': action, 'topic_ids': list(dict.fromkeys(arguments.get('topic_ids') or []))}
    if action == 'tasks':
        tasks = arguments.get('tasks')
        return {'action': action, 'topic_id': arguments.get('topic_id'),
                'tasks': [None] * len(tasks) if isinstance(tasks, list) else None}
    if action == 'fresh':
        return {'action': action, 'source_run_id': arguments.get('source_run_id'),
                'expected_version': arguments.get('expected_version')}
    if action == 'reference':
        return {'action': action, 'series_id': arguments.get('series_id'), 'title': arguments.get('title')}
    return {'action': action}


def valid_submission_identity(session, creator_id: str, arguments: dict, receipt: dict, identity) -> bool:
    """Bind tool receipts to the immutable records created by the same request."""
    if not valid_submission_shape(arguments, receipt):
        return False
    action = arguments.get('action')
    if action == 'start':
        return True
    if not isinstance(identity, dict) or not isinstance(identity.get('request_id'), str):
        return False
    request_id = identity['request_id']
    if action == 'fresh':
        run = session.get(ContentRun, receipt['run_id'])
        return bool(run and run.idempotency_key == 'fresh:' + request_id)
    recorded = session.get(WriteReceipt, request_id)
    if not recorded or recorded.operation != 'queue_topics' or not isinstance(recorded.response_json, dict):
        return False
    saved = recorded.response_json
    if saved.get('creator_id') != creator_id:
        return False
    if action == 'tasks':
        ids = [item['id'] for item in receipt['items']]
        saved_ids = saved.get('task_ids')
        if (recorded.resource_id != arguments.get('topic_id') or saved.get('kind') != 'production_tasks'
                or not isinstance(saved_ids, list) or len(saved_ids) != len(ids)
                or any(not isinstance(value, str) for value in saved_ids) or set(saved_ids) != set(ids)):
            return False
        for item in receipt['items']:
            task = session.get(ProductionTask, item['id'])
            run = session.get(ContentRun, item['run_id']) if item.get('run_id') else None
            if (not task or task.topic_id != arguments.get('topic_id') or task.create_request_id != request_id
                    or item.get('run_id') and (not run or run.task_id != task.id
                                               or run.idempotency_key != f'task:{task.id}')):
                return False
        return True
    if action == 'reference':
        ref = identity.get('reference_id')
        series_id = arguments.get('series_id')
        topic_id = reference_topic_id(series_id, request_id) if isinstance(series_id, str) else None
        run = session.get(ContentRun, receipt['run_id'])
        topic = session.get(Topic, topic_id) if topic_id else None
        detail = saved.get('reference_production')
        return bool(isinstance(ref, str) and ref and recorded.resource_id == series_id and run and topic
                    and topic.series_id == series_id and topic.reference_id == ref and run.topic_id == topic_id
                    and run.idempotency_key == 'content:' + topic_id
                    and (detail is None or isinstance(detail, dict) and detail.get('run_id') == run.id))
    return False


def submission_outcome(session, creator_id: str, arguments: dict, receipt: dict, identity=None) -> tuple[str, str | None]:
    """Verify the receipt and account-owned Run/Task before releasing a step."""
    if not valid_submission_identity(session, creator_id, arguments, receipt, identity):
        return 'unknown', '制作回执与请求不一致，请核对原任务。'
    action = arguments.get('action')
    items = receipt['items'] if action in {'start', 'tasks'} else [receipt]

    if any(item.get('status') == 'rejected' or item.get('error_code') for item in items):
        return 'unknown', '部分制作未受理或需要核对；已提交的任务保留，不自动重派。'
    observed = observe_production(session, creator_id, arguments, receipt)
    if observed.result is None:
        return 'unknown', (observed.terminal or ('unknown', '制作任务无法核对。'))[1]
    projected = observed.result.get('items') if action in {'start', 'tasks'} else [observed.result]
    for item in projected:
        run = session.get(ContentRun, item.get('run_id')) if item.get('run_id') else None
        if action == 'fresh' and run:
            event = session.scalar(select(ContentRunEvent).where(ContentRunEvent.content_run_id == run.id,
                ContentRunEvent.event_type == ContentRunEventType.CREATED).order_by(ContentRunEvent.id).limit(1))
            payload = event.payload_json if event and isinstance(event.payload_json, dict) else {}
            if (run.id == arguments.get('source_run_id')
                    or run.topic_id != run_topic_id(session, arguments.get('source_run_id'))
                    or payload.get('fresh_source') != {'run_id': arguments.get('source_run_id'),
                                                        'version': arguments.get('expected_version')}):
                return 'unknown', '重做任务与来源不匹配，请核对原任务。'
        if action == 'reference' and run:
            topic = session.get(Topic, run.topic_id)
            if (not topic or topic.series_id != arguments.get('series_id') or not topic.reference_id
                    or (run.input_snapshot_json or {}).get('topic_title') != arguments.get('title')):
                return 'unknown', '参考制作任务与原要求不匹配，请核对原任务。'
        if item.get('terminal_status') == 'unknown':
            return 'unknown', item.get('error') or '制作任务结果需要核对。'
        if item.get('terminal_status') in {'failed', 'interrupted', 'cancelled', 'needs_attention'}:
            return 'failed', item.get('error') or '部分制作未完成。'
        if item.get('terminal_status') == 'succeeded':
            continue
        if action == 'tasks' and not run:
            task = session.get(ProductionTask, item.get('id'))
            if task and task.dispatch_state == 'waiting':
                continue
        if run and (run.status.value in {'producing', 'validating'} or item.get('queue_status') in {'pending', 'running'}):
            continue
        return 'unknown', '没有可核验的生产授权或产物，请核对原任务。'
    return 'succeeded', None
