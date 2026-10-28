"""Durable dependency-aware execution of registered account tools.

Steps are host-owned state, not extra user messages. No tool results are treated
as instructions; references select data only from completed dependencies.
"""
from __future__ import annotations
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re
from threading import Event, RLock, Thread
from urllib.parse import urlsplit
from sqlalchemy import select, text

from creatoros.ai.types import ToolCall
from creatoros.context import RuntimeContext
from creatoros.config import PROJECT_ROOT
from creatoros.storage.models import AgentPlan, AgentPlanStep, ContentRun, ContentRevision, ProductionTask, Topic, Series, MediaCollection
from creatoros.runs.service import ContentRunError
from creatoros.tools.execution import execute_tool_call

# Only allow actions whose API scope and write replay semantics are known.
ALLOWED = {
    'start_content_run': None,
    'manage_topics': {'get', 'list', 'create', 'update', 'remove'},
    'manage_material': {'list', 'get', 'import_link'},
    'manage_media_collection': {'list', 'get', 'create', 'export'},
    'manage_skill_library': {'get', 'add'},
    'get_content_run': None,
    'get_creator_tasks': None,
    'get_producer_skill': None,
    # Host-owned checkpoint: no model call or side effect is executed here.
    'request_review': None,
}
STEP_KEY = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')


def _hash(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _spec(step):
    return {key: step[key] for key in ('id', 'title', 'tool', 'arguments', 'depends_on', 'wait_for_completion')}


def _validate_steps(steps):
    if not isinstance(steps, list) or not 1 <= len(steps) <= 100:
        raise ValueError('计划需要 1–100 个步骤。')
    known = set()
    for item in steps:
        if not isinstance(item, dict):
            raise ValueError('步骤格式无效。')
        key, tool, args, deps = item.get('id'), item.get('tool'), item.get('arguments'), item.get('depends_on', [])
        if not isinstance(key, str) or not STEP_KEY.fullmatch(key) or key in known:
            raise ValueError('步骤标识无效或重复。')
        if not isinstance(item.get('title'), str) or not 1 <= len(item['title']) <= 240:
            raise ValueError('请为每步填写名称。')
        if tool not in ALLOWED or not isinstance(args, dict):
            raise ValueError('该步骤不是支持的工具。')
        if ALLOWED[tool] is not None and args.get('action') not in ALLOWED[tool]:
            raise ValueError('该工具动作尚不支持计划执行。')
        if 'creator_id' in args:
            raise ValueError('账号由会话确定，不能在步骤中指定。')
        if tool == 'request_review' and (set(args) != {'question'} or not isinstance(args['question'], str)
                                         or not 1 <= len(args['question']) <= 2000):
            raise ValueError('检查点需要一条清晰的判断问题。')
        if not isinstance(deps, list) or len(set(deps)) != len(deps) or any(dep not in known for dep in deps):
            raise ValueError('依赖必须引用前面已定义的步骤。')
        if item.get('wait_for_completion', False) and tool != 'start_content_run':
            raise ValueError('当前仅支持等待生产任务完成。')
        known.add(key)


def _resolve(value, results, allowed):
    if isinstance(value, dict):
        if '$ref' in value:
            if set(value) != {'$ref'} or not isinstance(value['$ref'], str):
                raise ValueError('步骤引用格式无效。')
            parts = value['$ref'].split('.')
            if len(parts) < 2 or parts[0] not in allowed or parts[0] not in results:
                raise ValueError('只能引用已完成的依赖步骤。')
            def walk(current, rest):
                if not rest: return deepcopy(current)
                head, tail = rest[0], rest[1:]
                if head == '*' and isinstance(current, list):
                    return [walk(item, tail) for item in current]
                if not isinstance(current, dict) or head not in current:
                    raise ValueError('依赖结果中缺少所需字段。')
                return walk(current[head], tail)
            return walk(results[parts[0]], parts[1:])
        return {key: _resolve(item, results, allowed) for key, item in value.items()}
    if isinstance(value, list): return [_resolve(item, results, allowed) for item in value]
    return value


def _validate_plan_inputs(session, step, args, by_key):
    """Video plans must use an exact collection, never the topic's auto-collection."""
    production_deps = [by_key[k] for k in step.depends_on_json
                       if by_key[k].tool == 'start_content_run' and by_key[k].wait_for_completion]
    if step.tool == 'manage_media_collection' and args.get('action') == 'create' and production_deps:
        if args.get('topic_id'):
            raise ValueError('计划素材集必须按本次 Run 精确收图，不能自动收集整个选题。')
        ref = (step.arguments_json or {}).get('run_ids')
        if not isinstance(ref, dict) or set(ref) != {'$ref'}:
            raise ValueError('计划素材集的来源必须引用已完成的生产步骤。')
        source, _, path = ref['$ref'].partition('.')
        row = by_key.get(source)
        if (not row or row.tool != 'start_content_run' or not row.wait_for_completion
                or source not in step.depends_on_json or path != 'items.*.run_id'):
            raise ValueError('素材集只能使用本计划已经完成的生产图片。')
        if not args.get('run_ids') or any(not value for value in args['run_ids']):
            raise ValueError('本计划没有可收集的图片 Run。')
        for item in row.result_json.get('items', []):
            if item.get('kind', 'image') != 'image':
                raise ValueError('本计划收图步骤只能接收生图任务。')
            revisions = list(session.scalars(select(ContentRevision).where(ContentRevision.content_run_id == item.get('run_id'))))
            if (len(revisions) != 1 or revisions[0].id != item.get('revision_id')
                    or revisions[0].artifact_digest != item.get('artifact_digest')):
                raise ValueError('本次生图的版本已变化，请重新确认收图范围。')
    if step.tool == 'start_content_run':
        raw_tasks = (step.arguments_json or {}).get('tasks') or []
        tasks = args.get('tasks') or []
        for original, task in zip(raw_tasks, tasks):
            if task.get('kind', 'image') != 'video':
                continue
            ref = (original.get('inputs') or {}).get('collection_id')
            if not isinstance(ref, dict) or set(ref) != {'$ref'}:
                if production_deps:
                    raise ValueError('视频必须引用本计划创建的精确素材集。')
                continue
            source, _, path = ref['$ref'].partition('.')
            row = by_key.get(source)
            if (not row or row.tool != 'manage_media_collection' or row.arguments_json.get('action') != 'create'
                    or source not in step.depends_on_json or path not in {'id', 'collection_id'}):
                raise ValueError('视频素材集必须来自本计划的精确收图步骤。')
            collection = session.get(MediaCollection, task.get('inputs', {}).get('collection_id'))
            if (not collection or collection.topic_id is not None
                    or collection.version != (row.result_json or {}).get('version')):
                raise ValueError('本计划的素材集已发生变化，请重新确认后再剪辑。')


class PlanService:
    def __init__(self, database, chat):
        self.db, self.chat = database, chat
        self._lock, self._stop, self._wake = RLock(), Event(), Event()
        self._thread = None
        self._pool = None
        self._futures = {}
        self.on_continuation = None
        self.start_gate = lambda: True

    @contextmanager
    def _write(self):
        with self._lock, self.db.session() as session:
            if self.db.engine.dialect.name == 'sqlite': session.execute(text('BEGIN IMMEDIATE'))
            yield session

    def _owner(self, sid):
        doc = self.chat.get(sid)
        owner = doc.get('creator_id') if doc.get('scope_kind') == 'creator' else None
        if not owner: raise ContentRunError('请在账号对话中创建计划。', code='scope_conflict', status_code=403)
        self.chat.require_creator(owner)
        return owner

    @staticmethod
    def _url(url):
        parsed = urlsplit(url)
        if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'} or not parsed.port:
            raise ValueError('计划只能连接本机 Studio 服务。')
        return f'http://{parsed.netloc}'

    def create(self, sid, request_id, title, steps, studio_url):
        owner = self._owner(sid)
        normalized = [dict(id=item['id'], title=item['title'], tool=item['tool'],
                           arguments=item.get('arguments', {}), depends_on=item.get('depends_on', []),
                           wait_for_completion=item.get('wait_for_completion', False)) for item in steps]
        _validate_steps(normalized)
        signature = _hash({'title': title, 'steps': normalized})
        identity = _hash({'session_id': sid, 'request_id': request_id})
        with self._write() as session:
            old = session.get(AgentPlan, identity)
            if old:
                if old.request_digest != signature or old.creator_id != owner:
                    raise ContentRunError('同一请求的计划内容已变化。', code='idempotency_conflict', status_code=409)
            else:
                session.add(AgentPlan(id=identity, session_id=sid, creator_id=owner, request_id=request_id,
                    request_digest=signature, title=title, status='running', version=1, studio_url=self._url(studio_url),
                    continuation_state='none', continuation_version=0))
                for position, item in enumerate(normalized):
                    session.add(AgentPlanStep(id=identity + ':' + item['id'], plan_id=identity, step_key=item['id'],
                        title=item['title'], position=position, tool=item['tool'], arguments_json=item['arguments'],
                        depends_on_json=item['depends_on'], wait_for_completion=item['wait_for_completion'], status='pending'))
        self._wake.set()
        return self.get(sid, identity)

    def list(self, sid):
        owner = self._owner(sid)
        with self.db.session() as session:
            rows = session.scalars(select(AgentPlan).where(AgentPlan.session_id == sid, AgentPlan.creator_id == owner)
                                   .order_by(AgentPlan.created_at.desc())).all()
            return {'items': [dict(id=r.id, title=r.title, status=r.status, version=r.version,
                                   continuation_state=r.continuation_state, pause_reason=self._pause_reason(r)) for r in rows]}

    @staticmethod
    def _pause_reason(plan):
        if plan.status != 'paused': return None
        return 'checkpoint' if plan.continuation_state in {'pending', 'claimed', 'done_checkpoint'} else 'manual'

    @staticmethod
    def _find(session, sid, owner, identity):
        plan = session.get(AgentPlan, identity)
        if not plan or plan.session_id != sid or plan.creator_id != owner:
            raise ContentRunError('计划不存在或不属于当前会话。', code='not_found', status_code=404)
        return plan

    @staticmethod
    def _rows(session, identity):
        return list(session.scalars(select(AgentPlanStep).where(AgentPlanStep.plan_id == identity)
                                    .order_by(AgentPlanStep.position)))

    def get(self, sid, identity):
        owner = self._owner(sid)
        with self.db.session() as session:
            p = self._find(session, sid, owner, identity)
            rows = self._rows(session, identity)
            return dict(id=p.id, session_id=p.session_id, creator_id=p.creator_id, title=p.title,
                version=p.version, status=p.status, continuation_state=p.continuation_state, pause_reason=self._pause_reason(p),
                steps=[dict(id=s.step_key, title=s.title, tool=s.tool, arguments=deepcopy(s.arguments_json),
                    depends_on=list(s.depends_on_json), wait_for_completion=s.wait_for_completion,
                    status=s.status, result=deepcopy(s.result_json), error=s.error,
                    retryable=(s.status == 'failed' and s.tool in {'get_content_run', 'get_producer_skill'}
                               and (s.result_json or {}).get('_error_type') not in {'invalid_arguments', 'agent_scope_rejected'}),
                    skippable=self._skippable(s)) for s in rows])

    @staticmethod
    def _skippable(row):
        # Only reads and provably pre-dispatch validation failures can be skipped.
        # A write rejected by an external API may have succeeded: do not guess.
        if row.status != 'failed' or row.tool == 'start_content_run': return False
        if row.result_json is None: return True  # _resolve/_validate before dispatch
        return row.tool in {'get_content_run', 'get_producer_skill'} or (
            (row.result_json or {}).get('_error_type') in {'invalid_arguments', 'agent_scope_rejected'})

    def edit(self, sid, identity, expected_version, steps):
        owner = self._owner(sid)
        normalized = [dict(id=x['id'],title=x['title'],tool=x['tool'],arguments=x.get('arguments',{}),
            depends_on=x.get('depends_on',[]),wait_for_completion=x.get('wait_for_completion',False)) for x in steps]
        _validate_steps(normalized)
        with self._write() as session:
            p = self._find(session, sid, owner, identity)
            if p.version != expected_version: raise ContentRunError('计划已变化，请刷新。', code='plan_conflict', status_code=409)
            existing = {s.step_key:s for s in self._rows(session, identity)}
            supplied = {s['id']:s for s in normalized}
            prior_production = [s for s in existing.values() if s.tool == 'start_content_run' and s.status not in {'pending', 'blocked'}]
            added_production = [s for s in normalized if s['tool'] == 'start_content_run' and (
                s['id'] not in existing or (existing[s['id']].status in {'pending', 'blocked'} and
                (existing[s['id']].tool != s['tool'] or existing[s['id']].arguments_json != s['arguments'])))]
            if added_production and any(s.status in {'running', 'waiting', 'failed', 'unknown'} for s in prior_production):
                raise ContentRunError('已有生产仍在运行、失败或结果未知；请核对原任务，不能添加替代生产。', code='plan_conflict', status_code=409)
            for new in added_production:
                for old in prior_production:
                    previous = old.arguments_json or {}
                    current = new['arguments']
                    old_topics = set(previous.get('topic_ids') or []) | ({previous['topic_id']} if previous.get('topic_id') else set())
                    new_topics = set(current.get('topic_ids') or []) | ({current['topic_id']} if current.get('topic_id') else set())
                    if not old_topics & new_topics: continue
                    old_tasks, new_tasks = previous.get('tasks'), current.get('tasks')
                    old_images = [t for t in old_tasks or [] if isinstance(t, dict) and t.get('kind', 'image') == 'image']
                    new_images = [t for t in new_tasks or [] if isinstance(t, dict) and t.get('kind', 'image') == 'image']
                    if (old_tasks and not old_images) or (new_tasks and not new_images): continue  # video is not image replay
                    old_titles = {t.get('title') for t in old_images}
                    new_titles = {t.get('title') for t in new_images}
                    if not old_tasks or not new_tasks or None in old_titles or None in new_titles or old_titles & new_titles:
                        raise ContentRunError('该选题的同名生图任务已有提交记录，不能另建步骤重复提交。', code='plan_conflict', status_code=409)
            for key,row in existing.items():
                prior = dict(id=key,title=row.title,tool=row.tool,arguments=row.arguments_json,
                             depends_on=row.depends_on_json,wait_for_completion=row.wait_for_completion)
                if row.status not in {'pending', 'blocked'} and supplied.get(key) != prior:
                    raise ContentRunError('已开始的步骤不能修改或移除。', code='plan_conflict', status_code=409)
                if key not in supplied: session.delete(row)
            for pos,item in enumerate(normalized):
                row = existing.get(item['id'])
                if row is None:
                    row = AgentPlanStep(id=identity+':'+item['id'],plan_id=identity,step_key=item['id'],status='pending')
                    session.add(row)
                if row.status == 'blocked': row.status, row.error = 'pending', None
                row.title,row.position,row.tool = item['title'],pos,item['tool']
                row.arguments_json,row.depends_on_json,row.wait_for_completion = item['arguments'],item['depends_on'],item['wait_for_completion']
            p.version += 1
            if p.status in {'completed','needs_attention'}: p.status = 'running'
            if p.continuation_state not in {'claimed', 'done_checkpoint'}: p.continuation_state = 'none'
        self._wake.set()
        return self.get(sid, identity)

    def action(self, sid, identity, expected_version, action, step_id=None):
        owner = self._owner(sid)
        with self._write() as session:
            p = self._find(session, sid, owner, identity)
            if p.version != expected_version: raise ContentRunError('计划已变化，请刷新。', code='plan_conflict', status_code=409)
            if action == 'pause':
                if p.status != 'running': raise ValueError('当前计划不在运行。')
                p.status = 'paused'
            elif action == 'resume':
                if p.status != 'paused': raise ValueError('当前计划未暂停。')
                p.status = 'running'
                if p.continuation_state in {'pending', 'done_checkpoint'}: p.continuation_state = 'none'
            elif action == 'retry':
                row = next((s for s in self._rows(session, identity) if s.step_key == step_id), None)
                if (not row or row.status != 'failed' or row.tool not in {'get_content_run', 'get_producer_skill'}
                        or (row.result_json or {}).get('_error_type') in {'invalid_arguments', 'agent_scope_rejected'}):
                    raise ValueError('该步骤不能安全自动重试；请先检查原任务。')
                row.status, row.error, row.result_json = 'pending', None, None
                for blocked in self._rows(session, identity):
                    if blocked.status == 'blocked': blocked.status, blocked.error = 'pending', None
                p.status, p.continuation_state = 'running', 'none'
            elif action == 'skip':
                row = next((s for s in self._rows(session, identity) if s.step_key == step_id), None)
                if not row or not self._skippable(row):
                    raise ValueError('该步骤不能安全跳过；请先核对原任务。')
                # Preserve its error and result for audit. Dependants must be
                # explicitly rerouted; skip by itself does not start work.
                row.status = 'skipped'
            else: raise ValueError('不支持的计划操作。')
            p.version += 1
        self._wake.set()
        return self.get(sid, identity)

    def set_continuation_callback(self, callback):
        self.on_continuation = callback
        self._wake.set()

    def pending_continuation_sessions(self):
        now = datetime.now(timezone.utc)
        with self.db.session() as session:
            return list(dict.fromkeys(session.scalars(select(AgentPlan.session_id).where(
                (AgentPlan.continuation_state == 'pending') |
                ((AgentPlan.continuation_state == 'claimed') & (AgentPlan.continuation_until < now))))))

    def claim_continuation(self, session_id, worker_token, lease_seconds=60):
        now = datetime.now(timezone.utc)
        with self._write() as session:
            p = session.scalar(select(AgentPlan).where(AgentPlan.session_id == session_id,
                (AgentPlan.continuation_state == 'pending') |
                ((AgentPlan.continuation_state == 'claimed') & (AgentPlan.continuation_until < now)))
                .order_by(AgentPlan.updated_at))
            if not p: return None
            p.continuation_state, p.continuation_token = 'claimed', worker_token
            p.continuation_until = now + timedelta(seconds=lease_seconds)
            return dict(event_id=f'{p.id}:{p.continuation_version}', plan_id=p.id, session_id=p.session_id,
                        creator_id=p.creator_id, status=p.status, reason='checkpoint' if p.status == 'paused' else ('failure' if p.status == 'needs_attention' else 'completed'), studio_url=p.studio_url,
                        steps=[dict(id=s.step_key,title=s.title,status=s.status,result=deepcopy(s.result_json),error=s.error)
                               for s in self._rows(session,p.id)])

    def ack_continuation(self, plan_id, event_id, worker_token):
        with self._write() as session:
            p = session.get(AgentPlan, plan_id)
            if not p or event_id != f'{p.id}:{p.continuation_version}': return False
            if p.continuation_state in {'done', 'done_checkpoint'}: return True
            if p.continuation_state != 'claimed' or p.continuation_token != worker_token: return False
            p.continuation_state, p.continuation_token, p.continuation_until = ('done_checkpoint' if p.status == 'paused' else 'done'), None, None
            return True

    def release_continuation(self, plan_id, event_id, worker_token):
        with self._write() as session:
            p = session.get(AgentPlan, plan_id)
            if not p or event_id != f'{p.id}:{p.continuation_version}' or p.continuation_token != worker_token:
                return False
            p.continuation_state, p.continuation_token, p.continuation_until = 'pending', None, None
        self._wake.set()
        return True

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive(): return
            self._stop.clear()
            # Interrupted calls are never guessed successful. Production submissions
            # are replayed with the same host-owned idempotency identity.
            with self._write() as session:
                for row in session.scalars(select(AgentPlanStep).where(AgentPlanStep.status == 'running')):
                    if row.tool == 'start_content_run' and isinstance(row.arguments_json.get('tasks'), list):
                        row.status = 'pending'
                    else:
                        row.status, row.error = 'unknown', '服务中断时步骤结果未知，请先核对。'
            self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='creatoros-plan')
            self._thread = Thread(target=self._loop, daemon=True, name='creatoros-plan-scheduler')
            self._thread.start()

    def stop(self, timeout=10):
        self._stop.set(); self._wake.set()
        if self._thread: self._thread.join(timeout=timeout)
        if self._pool: self._pool.shutdown(wait=False, cancel_futures=True)

    shutdown = stop

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.advance()
                if self.on_continuation and self.pending_continuation_sessions():
                    self.on_continuation()
            except Exception:
                import logging
                logging.getLogger(__name__).exception('Plan scheduler failed')
            self._wake.wait(1); self._wake.clear()

    def advance(self):
        if not self.start_gate(): return
        # DB checks and claims are short; actual tools run outside every lock.
        dispatch = []
        with self._write() as session:
            if not self.start_gate(): return
            plans = list(session.scalars(select(AgentPlan).where(AgentPlan.status == 'running')))
            for p in plans:
                rows = self._rows(session, p.id)
                by_key = {s.step_key:s for s in rows}
                for s in rows:
                    if s.status == 'waiting':
                        terminal = self._watch(session, p, s)
                        if terminal:
                            s.status, s.error = terminal
                            p.version += 1
                for s in rows:
                    if s.status != 'pending': continue
                    deps = [by_key[k] for k in s.depends_on_json]
                    if any(d.status in {'failed','unknown','blocked','skipped'} for d in deps):
                        s.status, s.error = 'blocked', '依赖步骤未完成。'
                        p.version += 1
                    elif all(d.status == 'succeeded' for d in deps):
                        if s.tool == 'request_review':
                            s.status, s.result_json = 'succeeded', {'question': s.arguments_json['question']}
                            p.status, p.continuation_state = 'paused', 'pending'
                            p.continuation_version += 1; p.version += 1
                            # Already claimed independent work may finish, but no
                            # later step is started until an explicit resume.
                            break
                        try:
                            result_data = {key:by_key[key].result_json for key in s.depends_on_json}
                            args = _resolve(s.arguments_json,result_data,set(s.depends_on_json))
                            _validate_plan_inputs(session, s, args, by_key)
                        except ValueError as error:
                            s.status, s.error = 'failed', str(error)
                            p.version += 1
                            continue
                        s.status = 'running'; p.version += 1
                        dispatch.append((p.id,s.step_key,p.session_id,p.creator_id,p.studio_url,s.tool,args))
                if p.status == 'running' and rows and all(s.status in {'succeeded','failed','blocked','unknown','skipped'} for s in rows):
                    p.status = 'completed' if all(s.status in {'succeeded','skipped'} for s in rows) else 'needs_attention'
                    p.continuation_state = 'pending'; p.continuation_version += 1; p.version += 1
        for item in dispatch:
            if self._pool:
                future = self._pool.submit(self._execute, *item)
                future.add_done_callback(lambda _: self._wake.set())

    def _execute(self, plan_id, step_id, sid, creator_id, studio_url, tool, arguments):
        from creatoros.web.chat import ACCOUNT_TOOLS
        context = RuntimeContext(project_root=PROJECT_ROOT, studio_url=studio_url, creator_id=creator_id,
            agent_session_id=sid, session_file=self.chat._path(sid).with_name('messages.json'),
            user_request_id=f'plan:{plan_id}:{step_id}', allowed_tools=ACCOUNT_TOOLS)
        try:
            result = execute_tool_call(ToolCall('plan-' + step_id, tool, json.dumps(arguments, ensure_ascii=False)),
                                       context=context, model_requested=True)
        except Exception:
            # A crashed caller may already have caused side effects. Do not retry.
            with self._write() as session:
                row = session.get(AgentPlanStep, plan_id+':'+step_id)
                p = session.get(AgentPlan, plan_id)
                if row and p and row.status == 'running':
                    row.status, row.error = 'unknown', '步骤执行中断，结果未知，请先核对。'
                    p.version += 1
            self._wake.set()
            return
        try: data = json.loads(result.model_content or result.content)
        except (ValueError, TypeError): data = {'message': str(result.model_content or result.content)[:4000]}
        with self._write() as session:
            row = session.get(AgentPlanStep, plan_id+':'+step_id)
            p = session.get(AgentPlan, plan_id)
            if not row or not p or row.status != 'running': return
            if result.is_error:
                unknown = result.error_type in {'studio_outcome_unknown','studio_invalid_response','tool_exception'}
                # An HTTP error from a write can arrive after the server committed.
                # Without a receipt it is never evidence that production did not start.
                if row.tool == 'start_content_run' and result.error_type not in {
                        'invalid_arguments', 'agent_scope_rejected', 'studio_unavailable'}:
                    unknown = True
                data['_error_type'] = result.error_type
                row.status = 'unknown' if unknown else 'failed'
                row.error = str(data.get('message') or result.error_type or '步骤失败')[:2000]
                row.result_json = data
            else:
                row.result_json = data
                row.status = 'waiting' if row.wait_for_completion else 'succeeded'
            p.version += 1
        self._wake.set()

    @staticmethod
    def _watch(session, plan, step):
        data = deepcopy(step.result_json or {})
        task_ids = [item.get('id') for item in data.get('items', []) if isinstance(item, dict) and item.get('id')]
        if not task_ids and data.get('run_id'):
            run = session.get(ContentRun, data['run_id'])
            topic = session.get(Topic, run.topic_id) if run else None
            series = session.get(Series, topic.series_id) if topic else None
            if not series or series.creator_id != plan.creator_id:
                return ('failed', '制作任务不存在或账号范围不符。')
            if run.status.value in {'failed', 'interrupted', 'cancelled'}:
                data['terminal_status'], data['error'] = run.status.value, run.error_message or '制作任务未完成。'
                step.result_json = data
                return ('failed', data['error'])
            if run.status.value not in {'awaiting_approval', 'approved', 'published'}: return None
            revision = session.scalar(select(ContentRevision).where(ContentRevision.content_run_id == run.id,
                ContentRevision.revision_number == run.active_revision_number))
            if not revision or not revision.artifact_digest: return ('failed', '制作任务没有可核验产物。')
            if data.get('revision_id') and (data['revision_id'] != revision.id or data.get('artifact_digest') != revision.artifact_digest):
                return ('failed', '制作任务产物版本发生变化，请重新核对。')
            data.update(revision_id=revision.id, artifact_digest=revision.artifact_digest, terminal_status='succeeded')
            step.result_json = data
            return ('succeeded', None)
        if not task_ids: return ('failed','未获得可等待的制作任务。')
        pending, failed = False, []
        for identity in task_ids:
            task = session.get(ProductionTask, identity)
            topic = session.get(Topic, task.topic_id) if task else None
            series = session.get(Series, topic.series_id) if topic else None
            if not series or series.creator_id != plan.creator_id:
                return ('failed','制作任务不存在或账号范围不符。')
            item = next(x for x in data['items'] if x.get('id') == identity)
            expected_run = item.get('run_id')
            run = session.get(ContentRun, expected_run) if expected_run else session.scalar(
                select(ContentRun).where(ContentRun.task_id == identity).order_by(ContentRun.created_at).limit(1))
            if run and run.task_id != identity: return ('failed','制作任务与产物不匹配。')
            if run and not expected_run:
                item['run_id'] = run.id
                step.result_json = deepcopy(data)
            if not run:
                if task.dispatch_state in {'needs_attention','cancelled'}:
                    item['terminal_status'], item['error'] = task.dispatch_state, task.last_error or '任务未完成。'
                    failed.append(item['error'])
                else:
                    pending = True
                    item.pop('terminal_status', None); item.pop('error', None)
                continue
            if run.status.value in {'failed','interrupted','cancelled'}:
                item['terminal_status'], item['error'] = run.status.value, run.error_message or '制作任务未完成。'
                failed.append(item['error'])
                continue
            if run.status.value not in {'awaiting_approval','approved','published'}:
                pending = True
                item.pop('terminal_status', None); item.pop('error', None)
                continue
            revision = session.scalar(select(ContentRevision).where(ContentRevision.content_run_id==run.id,
                ContentRevision.revision_number==run.active_revision_number))
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
        step.result_json = data
        if pending: return None
        if failed: return ('failed', f'{len(failed)} 项制作未完成；其余已完成项目已保留。' + failed[0])
        return ('succeeded',None)
