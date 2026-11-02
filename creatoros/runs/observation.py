"""Read production progress for orchestration without exposing ORM entities.

The caller provides its active database session: observing and persisting a plan
step therefore happen in the same transaction. This module never dispatches,
retries or modifies a production task.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from sqlalchemy import select

from creatoros.storage.models import ContentRevision, ContentRun, ProductionTask, Series, Topic


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

    pending, failed = False, []
    for item in items:
        if not isinstance(item, dict):
            return ProductionObservation(('failed', '制作回执格式无效。'), None)
        identity, task = item.get('id'), None
        expected_run = item.get('run_id')
        run = session.get(ContentRun, expected_run) if expected_run else None
        if action == 'tasks':
            task = session.get(ProductionTask, identity) if identity else None
            if (not task or (expected_run and not run)
                    or (run and (run.task_id != identity or run.topic_id != task.topic_id))):
                return ProductionObservation(('failed', '制作任务与产物不匹配。'), None)
            if not run:
                run = session.scalar(select(ContentRun).where(ContentRun.task_id == identity)
                                     .order_by(ContentRun.created_at).limit(1))
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
        if not run:
            if task and task.dispatch_state not in {'needs_attention', 'cancelled'}:
                pending = True
                item.pop('terminal_status', None)
                item.pop('error', None)
            else:
                state = task.dispatch_state if task else 'failed'
                message = task.last_error if task else item.get('message')
                item['terminal_status'], item['error'] = state, message or '未获得可等待的制作任务。'
                failed.append(item['error'])
            continue
        if run.status.value in {'failed', 'interrupted', 'cancelled'}:
            item['terminal_status'], item['error'] = run.status.value, run.error_message or '制作任务未完成。'
            failed.append(item['error'])
            continue
        if run.status.value not in {'awaiting_approval', 'approved', 'published'}:
            pending = True
            item.pop('terminal_status', None)
            item.pop('error', None)
            continue
        revision = session.scalar(select(ContentRevision).where(ContentRevision.content_run_id == run.id,
            ContentRevision.revision_number == run.active_revision_number))
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
    if failed:
        return ProductionObservation(('failed', f'{len(failed)} 项制作未完成；其余已完成项目已保留。' + failed[0]), data)
    return ProductionObservation(('succeeded', None), data)
