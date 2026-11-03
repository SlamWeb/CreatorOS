"""Immutable inputs and database scope captured before a production commit."""
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from creatoros.storage import Creator, CreatorSkillRef, Series, SeriesSkillRef
from .models import ContentRunInput
from .task_contracts import TaskExecutionSpec
from .task_execution import verify_execution_spec
from creatoros.integrations.producer_skills import _digest


def production_scope(session, topic, series, task=None):
    """Database facts that can affect a prepared input or its authorization."""
    creator = session.get(Creator, series.creator_id) if series.creator_id else None
    account = session.get(CreatorSkillRef, series.creator_id) if series.creator_id else None
    pool = session.get(SeriesSkillRef, series.id)
    bindings = session.scalars(select(Series).where(Series.creator_id == series.creator_id).order_by(Series.id))
    return {
        "topic": [topic.id, topic.series_id, topic.title, topic.brief, topic.reference_id,
                  topic.source.value, topic.status.value],
        "series": [series.id, series.creator_id, series.revision, series.name, series.description,
                   series.audience, series.is_active],
        "creator_active": bool(creator and creator.is_active),
        "creator_input": ([creator.display_name, creator.platform.value] if creator else None),
        "account_version": account.version if account else 0,
        "account_ids": list(account.skill_ids_json) if account else [],
        "bindings": [[row.id, row.revision, row.skill_name, row.mind_skill_id, row.production_skill_id]
                     for row in bindings],
        "pool": ([pool.version, deepcopy(pool.skill_ids_json), deepcopy(pool.defaults_json)] if pool else None),
        "task": [task.id, task.topic_id, task.title, task.brief, task.collection_id] if task else None,
    }


@dataclass(frozen=True)
class PreparedRun:
    topic_id: str
    task_id: str | None
    key: str
    run_id: str
    revision_id: str
    snapshot: ContentRunInput
    scope: dict


@dataclass(frozen=True)
class PreparedTask:
    task: object
    staged: bool
    dispatch_inputs: tuple | None
    run: PreparedRun | None
    input_scope: dict | None


@dataclass(frozen=True)
class PreparedTasks:
    scope: dict
    previous_collection_id: str | None
    collection: object | None
    position: int
    items: tuple[PreparedTask, ...]


def run_skill_ids(prepared):
    snapshot = prepared.snapshot
    if snapshot.execution_spec is not None:
        return [skill.skill_id for skill in snapshot.execution_spec.skills]
    if snapshot.composition is not None:
        return [snapshot.composition.mind.id, snapshot.composition.production.id]
    return [snapshot.skill_name] if snapshot.skill_name else []


def task_skill_ids(prepared):
    identities = []
    for item in prepared.items:
        if item.staged:
            spec = TaskExecutionSpec.model_validate(item.task.definition_json['execution_spec'])
            identities.extend(skill.skill_id for skill in spec.skills)
        elif item.run is not None:
            identities.extend(run_skill_ids(item.run))
    return list(dict.fromkeys(identities))


def verify_prepared_run(runs, prepared, *, catalog=None, require_frozen=True):
    """Recheck frozen bytes before entering the SQLite writer transaction."""
    snapshot = prepared.snapshot
    root = runs.output_root / snapshot.creator_id / snapshot.series_id / prepared.run_id
    if snapshot.task_references is not None:
        from creatoros.integrations.topic_references import TopicReferenceStore
        TopicReferenceStore(runs.database).verify(snapshot.task_references)
    if snapshot.execution_spec is not None:
        verify_execution_spec(snapshot.execution_spec, root, ready=True)
    elif snapshot.composition is not None and require_frozen:
        for role in ('mind', 'production'):
            directory = root / 'skill-snapshot' / 'skills' / role
            if _digest(directory) != getattr(snapshot.composition, role).digest:
                raise ValueError('准备的 Skill 快照已变化。')
    elif snapshot.skill_path and snapshot.skill_digest:
        if _digest(Path(snapshot.skill_path)) != snapshot.skill_digest:
            raise ValueError('准备的 Skill 快照已变化。')
    if catalog is not None:
        if snapshot.execution_spec is not None:
            versions = [(skill.skill_id, skill.digest) for skill in snapshot.execution_spec.skills]
        elif snapshot.composition is not None:
            versions = [(skill.id, skill.digest) for skill in (snapshot.composition.mind, snapshot.composition.production)]
        else:
            versions = [(snapshot.skill_name, snapshot.skill_digest)] if snapshot.skill_name and snapshot.skill_digest else []
        for skill_id, digest in versions:
            current = (_digest(Path(catalog.resolve(skill_id))) if skill_id == catalog.BUILTIN_ID
                       else catalog.describe(skill_id)['digest'])
            if current != digest:
                raise ValueError('准备后 Skill 已变化，请重新核对本次选择。')


def verify_prepared_tasks(runs, stages, prepared):
    selected = {}

    def record(skill_id, digest):
        if skill_id in selected and selected[skill_id] != digest:
            raise ValueError('同一 Skill 的准备摘要不一致。')
        selected[skill_id] = digest

    for item in prepared.items:
        if item.staged:
            spec = TaskExecutionSpec.model_validate(item.task.definition_json['execution_spec'])
            verify_execution_spec(spec, stages.root / item.task.id)
            for skill in spec.skills:
                record(skill.skill_id, skill.digest)
        if item.run is not None:
            verify_prepared_run(runs, item.run)
            snapshot = item.run.snapshot
            if snapshot.execution_spec is not None:
                for skill in snapshot.execution_spec.skills:
                    record(skill.skill_id, skill.digest)
            elif snapshot.composition is not None:
                for skill in (snapshot.composition.mind, snapshot.composition.production):
                    record(skill.id, skill.digest)
            elif snapshot.skill_name and snapshot.skill_digest:
                record(snapshot.skill_name, snapshot.skill_digest)
    for skill_id, digest in selected.items():
        current = (_digest(Path(stages.catalog.resolve(skill_id))) if skill_id == stages.catalog.BUILTIN_ID
                   else stages.catalog.describe(skill_id)['digest'])
        if current != digest:
            raise ValueError('准备后 Skill 已变化，请重新核对本次选择。')
