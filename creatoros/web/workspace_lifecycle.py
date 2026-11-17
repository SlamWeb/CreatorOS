from sqlalchemy import select, func, text
from creatoros.agent.plans import CONTRACT_PREFIX
from creatoros.storage import Creator, Series, Topic, ContentRun, ContentRunStatus
from creatoros.storage.models import Base, ProductionQueueItem, ProductionTask, SkillIteration, AgentPlan, SkillCalibration, TopicResearchQueueItem, PublicationRequest
from creatoros.integrations.skill_iterations import ACTIVE as ITERATION_ACTIVE
from creatoros.integrations.skill_extraction import ExtractionError
from .composition import CompositionError


class WorkspaceLifecycle:
    """Remove workspace entries only after rechecking work and durable references."""
    def __init__(self, database, research, chat, discussions, extractions, materials):
        self.db, self.research, self.chat = database, research, chat
        self.discussions, self.extractions, self.materials = discussions, extractions, materials

    def _refs(self, session, column, value):
        # Include cascading and non-FK historical links, including future playlist tables.
        count = 0
        for table in Base.metadata.tables.values():
            if table.name in {'creators', 'series', 'write_receipts'} or column not in table.c:
                continue
            count += int(session.scalar(select(func.count()).select_from(table).where(table.c[column] == value)) or 0)
        return count

    def _series_assessment(self, session, series):
        sid = series.id
        active_statuses = (ContentRunStatus.PRODUCING, ContentRunStatus.VALIDATING)
        run_count = int(session.scalar(select(func.count()).select_from(ContentRun).join(Topic).where(
            Topic.series_id == sid, ContentRun.status.in_(active_statuses))) or 0)
        queue_count = int(session.scalar(select(func.count()).select_from(ProductionQueueItem).join(ContentRun).join(Topic).where(
            Topic.series_id == sid, ProductionQueueItem.status.in_(("pending", "running")),
            ContentRun.status.not_in(active_statuses))) or 0)
        staged_count = int(session.scalar(select(func.count()).select_from(ProductionTask).join(Topic).where(
            Topic.series_id == sid, ProductionTask.archived_at.is_(None), ProductionTask.dispatch_state == 'waiting')) or 0)
        research_count = int(session.scalar(select(func.count()).select_from(TopicResearchQueueItem).where(
            TopicResearchQueueItem.series_id == sid, TopicResearchQueueItem.status.in_(['queued', 'researching']))) or 0)
        if not research_count and self.research.has_active_for_series(sid):
            research_count = 1
        publishing_count = int(session.scalar(select(func.count()).select_from(PublicationRequest)
            .join(ContentRun, PublicationRequest.content_run_id == ContentRun.id).join(Topic).where(
                Topic.series_id == sid, PublicationRequest.status.in_(("sending", "verifying")))) or 0)
        iteration_count = int(session.scalar(select(func.count()).select_from(SkillIteration).where(
            SkillIteration.series_id == sid, SkillIteration.status.in_(ITERATION_ACTIVE))) or 0)
        records = self.discussions._records() if self.discussions else []
        discussions = sum(1 for row in records if row.get("series_id") == sid and
                          (row.get("status") == "running" or (row.get("status") == "queued" and row.get("queue_version") == 1)))
        # An unreadable file has unknown scope and activity. Fail closed rather
        # than deleting an entry that could still own that task.
        unreadable_discussions = max(0, len(list((self.discussions.root / "records").glob("*.json"))) - len(records)) if self.discussions else 0
        calibrations = 0
        for calibration in session.scalars(select(SkillCalibration).where(SkillCalibration.series_id == sid)):
            if calibration.status == 'applying':
                calibrations += 1
            elif self.extractions and calibration.status == 'draft':
                try:
                    job = self.extractions._read(calibration.job_id)
                    if job.get('status') in {'queued', 'running'} or job.get('operation'):
                        calibrations += 1
                except (OSError, ValueError, ExtractionError):
                    # Missing or damaged job cannot prove it has stopped.
                    calibrations += 1
        reasons = [{'code': code, 'count': count} for code, count in (
            ('active_runs', run_count), ('authorized_queue', queue_count), ('waiting_tasks', staged_count),
            ('research', research_count), ('skill_iterations', iteration_count), ('publishing', publishing_count),
            ('discussions', discussions), ('discussion_unreadable', unreadable_discussions),
            ('skill_calibrations', calibrations)) if count]
        refs = self._refs(session, 'series_id', sid)
        history = bool(refs or self.research.has_history_for_series(sid))
        return {'id': sid, 'status': 'blocked' if reasons else ('archived' if history else 'deleted'), 'reasons': reasons}

    def _creator_assessment(self, session, creator):
        children = list(session.scalars(select(Series).where(Series.creator_id == creator.id)))
        totals = {}
        for child in children:
            for reason in self._series_assessment(session, child)['reasons']:
                totals[reason['code']] = totals.get(reason['code'], 0) + reason['count']
        plan_count = int(session.scalar(select(func.count()).select_from(AgentPlan).where(
            AgentPlan.creator_id == creator.id, AgentPlan.request_digest.startswith(CONTRACT_PREFIX),
            ((AgentPlan.status == 'running') | AgentPlan.continuation_state.in_(['pending', 'claimed', 'executing'])))) or 0)
        if plan_count:
            totals['agent_plans'] = plan_count
        turn_count = self.chat.active_turns_for_creator(creator.id)
        if turn_count:
            totals['agent_chat'] = turn_count
        discussion_history = []
        if self.discussions:
            all_discussions = self.discussions._records()
            discussion_history = [row for row in all_discussions if row.get('creator_id') == creator.id]
            unscoped = max(0, len(list((self.discussions.root / 'records').glob('*.json'))) - len(all_discussions))
            if unscoped:
                totals['discussion_unreadable'] = unscoped
            active_discussions = sum(1 for row in discussion_history if row.get('status') == 'running'
                or (row.get('status') == 'queued' and row.get('queue_version') == 1))
            if active_discussions:
                totals['discussions'] = max(totals.get('discussions', 0), active_discussions)
        material_count = self.materials.active_jobs_for_creator(creator.id)
        if material_count:
            totals['material_jobs'] = material_count
        chats = self.chat.list(scope_kind='creator', creator_id=creator.id)
        history = bool(children or chats or discussion_history or self.materials.has_import_history(creator.id)
                       or self._refs(session, 'creator_id', creator.id))
        reasons = [{'code': k, 'count': v} for k, v in totals.items()]
        return {'id': creator.id, 'status': 'blocked' if reasons else ('archived' if history else 'deleted'), 'reasons': reasons}

    def assess(self, kind, identifier, session=None):
        if session is None:
            with self.db.session() as s:
                return self.assess(kind, identifier, s)
        cls = Creator if kind == 'creator' else Series
        item = session.get(cls, identifier)
        if item is None:
            raise CompositionError('账号或栏目不存在。', status_code=404, code='not_found')
        if not item.is_active:
            raise CompositionError('已移出工作区。', status_code=409, code='inactive')
        if kind == 'series' and item.creator_id and (item.creator is None or not item.creator.is_active):
            raise CompositionError('所属账号已移出工作区。', status_code=409, code='creator_inactive')
        return self._creator_assessment(session, item) if kind == 'creator' else self._series_assessment(session, item)

    def remove_creator(self, identifier, expected_status=None):
        with self.db.session() as session:
            if self.db.engine.dialect.name == 'sqlite':
                session.execute(text('BEGIN IMMEDIATE'))
            assessment = self.assess('creator', identifier, session)
            if assessment['status'] == 'blocked':
                raise CompositionError('账号仍有进行中或已授权排队任务，请等待结束后重试。', status_code=409, code='active_work')
            if expected_status is not None and assessment['status'] != expected_status:
                raise CompositionError('账号保留状态已变化，请重新查看后确认。', status_code=409, code='removal_changed')
            item = session.get(Creator, identifier)
            if assessment['status'] == 'deleted':
                session.delete(item)
            else:
                item.is_active = False
            session.flush()
            return {'ok': True, **assessment}

    def restore(self, kind, identifier):
        with self.db.session() as session:
            if self.db.engine.dialect.name == 'sqlite':
                session.execute(text('BEGIN IMMEDIATE'))
            cls = Creator if kind == 'creator' else Series
            item = session.get(cls, identifier)
            if item is None:
                raise CompositionError('账号或栏目不存在。', status_code=404, code='not_found')
            if kind == 'series' and item.creator_id and (item.creator is None or not item.creator.is_active):
                raise CompositionError('请先恢复所属账号。', status_code=409, code='creator_inactive')
            item.is_active = True
            session.flush()
            return {'id': identifier, 'status': 'restored'}
