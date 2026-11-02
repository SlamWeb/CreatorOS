"""Agent plan edits use the real isolated HTTP/SQLite path; no model calls."""
from pathlib import Path
from tempfile import TemporaryDirectory
from dataclasses import replace
from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from creatoros.ai.types import StreamEnd, TextDelta, ToolCall, ToolCallDelta
from creatoros.agent.loop import _unknown_continuation_write
from creatoros.context import RuntimeContext
from creatoros.storage.models import AgentPlan, AgentPlanStep
from creatoros.tools.execution import execute_tool_call
from creatoros.tools.definitions import tool_registry
from creatoros.tools.plans import _same_step_definition, _validate_argument_shape
from tests.agent_studio_support import serve
from tests.studio_review_fixtures import make_fixture


class ScriptedProvider:
    """Only the model is controlled; HTTP, SQLite and the Agent loop are real."""
    def __init__(self, turns):
        self.turns, self.calls = turns, 0
        self.client = SimpleNamespace(close=lambda: None)

    def stream(self, context):
        calls = self.turns[self.calls]
        self.calls += 1
        if not calls:
            yield TextDelta('已处理。')
            yield StreamEnd('stop')
            return
        for index, arguments in enumerate(calls):
            yield ToolCallDelta(index, f'call-{self.calls}-{index}', 'manage_plan', json.dumps(arguments))
        yield StreamEnd('tool_calls')


class PlanIncrementalHttpTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="plan-incremental-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db, _, self.producer, self.app = make_fixture(self.root)
        self.addCleanup(self.db.close)
        self.addCleanup(self.app.state.chat.shutdown)
        self.addCleanup(self.app.state.agent_plans.shutdown)
        self.app.state.agent_plans.start_gate = lambda: False
        self.app.state.agent_plans.set_continuation_callback(None)
        self.server = serve(self.app)
        self.url = self.server.__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        self.client = httpx.Client(base_url=self.url, timeout=10, trust_env=False)
        self.addCleanup(self.client.close)
        response = self.client.post('/api/agent/sessions', json={'creator_id': 'review-lab'})
        self.assertEqual(response.status_code, 201, response.text)
        self.sid = response.json()['id']
        self.path = f'/api/agent/sessions/{self.sid}/plans'
        self.steps = [
            {'id': 'first', 'title': '已执行读取', 'tool': 'manage_topics',
             'arguments': {'action': 'list', 'series_id': 'agent-notes'}},
            {'id': 'second', 'title': '等待调整', 'tool': 'manage_topics',
             'arguments': {'action': 'list', 'series_id': 'agent-notes'}, 'depends_on': ['first']},
            {'id': 'third', 'title': '保留步骤', 'tool': 'get_producer_skill',
             'arguments': {'skill_id': 'missing--0000000000000000'}, 'depends_on': ['second']},
        ]
        created = self.client.post(self.path, json={'request_id': str(uuid4()), 'title': '验证局部修改', 'steps': self.steps})
        self.assertEqual(created.status_code, 200, created.text)
        self.pid, self.version = created.json()['id'], created.json()['version']
        with self.db.session() as session:
            row = session.get(AgentPlanStep, self.pid + ':first')
            row.status, row.result_json = 'succeeded', {'value': 'must survive'}
            session.get(AgentPlan, self.pid).status = 'paused'
        self.context = RuntimeContext(project_root=self.root, studio_url=self.url, creator_id='review-lab',
            agent_session_id=self.sid, allowed_tools=frozenset({'manage_plan'}))

    def call(self, **args):
        return execute_tool_call(ToolCall(str(uuid4()), 'manage_plan', json.dumps(args, ensure_ascii=False)),
                                 context=self.context, model_requested=True)

    def read(self):
        response = self.client.get(f'{self.path}/{self.pid}')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_ordinary_agent_partial_update_preserves_omitted_history_and_dependencies(self):
        changed = {**self.steps[1], 'title': '调整后的读取'}
        result = self.call(action='update', plan_id=self.pid, expected_version=self.version, steps=[changed])
        self.assertFalse(result.is_error, result)
        plan = self.read()
        self.assertEqual([step['id'] for step in plan['steps']], ['first', 'second', 'third'])
        self.assertEqual(plan['steps'][0]['status'], 'succeeded')
        self.assertEqual(plan['steps'][0]['result'], {'value': 'must survive'})
        self.assertEqual(plan['steps'][1]['title'], '调整后的读取')
        self.assertEqual(plan['steps'][1]['depends_on'], ['first'])
        self.assertEqual(plan['steps'][2]['depends_on'], ['second'])
        self.assertEqual(self.producer.calls, 0)

    def test_real_validation_rejection_is_proven_uncommitted_not_unknown(self):
        before = self.read()
        invalid = {**self.steps[1], 'depends_on': ['missing']}
        result = self.call(action='update', plan_id=self.pid, expected_version=self.version, steps=[invalid])
        self.assertTrue(result.is_error)
        self.assertEqual(result.error_type, 'plan_validation_rejected', result)
        self.assertFalse(result.details.get('plan_write_outcome_unknown'), result)
        self.assertEqual(self.read(), before)

    def test_ordinary_http_422_after_commit_remains_unknown(self):
        original = self.app.state.agent_plans.get
        def fail_after_commit(*args):
            plan = original(*args)
            if plan['version'] > self.version:
                raise ValueError('controlled post-commit readback failure')
            return plan
        changed = {**self.steps[1], 'title': '实际已写入'}
        with patch.object(self.app.state.agent_plans, 'get', side_effect=fail_after_commit):
            result = self.call(action='update', plan_id=self.pid, expected_version=self.version, steps=[changed])
        self.assertEqual(result.error_type, 'invalid_request', result)
        self.assertTrue(result.details.get('plan_write_outcome_unknown'), result)
        after = self.read()
        self.assertEqual(after['version'], self.version + 1)
        self.assertEqual(after['steps'][1]['title'], changed['title'])

    def test_continuation_http_422_after_commit_remains_unknown(self):
        self.context = replace(self.context, plan_continuation_id=self.pid)
        original = self.app.state.agent_plans.get
        def fail_after_commit(*args):
            plan = original(*args)
            if plan['version'] > self.version:
                raise ValueError('controlled post-commit readback failure')
            return plan
        arguments = {'action': 'update', 'plan_id': self.pid, 'expected_version': self.version,
                     'steps': [{**self.steps[1], 'title': '续接实际已写入'}]}
        call = ToolCall(str(uuid4()), 'manage_plan', json.dumps(arguments))
        with patch.object(self.app.state.agent_plans, 'get', side_effect=fail_after_commit):
            result = execute_tool_call(call, context=self.context, model_requested=True)
        self.assertEqual(result.error_type, 'invalid_request', result)
        self.assertTrue(result.details.get('plan_write_outcome_unknown'), result)
        self.assertTrue(_unknown_continuation_write(call, result, self.context))
        after = self.read()
        self.assertEqual(after['version'], self.version + 1)
        self.assertEqual(after['steps'][1]['title'], '续接实际已写入')

    def test_new_id_appends_old_id_replaces_and_unmentioned_steps_remain(self):
        replacement = {**self.steps[1], 'title': '同 ID 换成读取 Skill', 'tool': 'get_producer_skill',
                       'arguments': {'skill_id': 'different--0000000000000000'}}
        added = {'id': 'new', 'title': '新加的读取', 'tool': 'manage_topics',
                 'arguments': {'action': 'list', 'series_id': 'agent-notes'}, 'depends_on': ['third']}
        result = self.call(action='update', plan_id=self.pid, expected_version=self.version,
                           steps=[replacement, added])
        self.assertFalse(result.is_error, result)
        plan = self.read()
        self.assertEqual([step['id'] for step in plan['steps']], ['first', 'second', 'third', 'new'])
        self.assertEqual(plan['steps'][1]['title'], replacement['title'])
        self.assertEqual(plan['steps'][1]['tool'], 'get_producer_skill')
        self.assertEqual(plan['steps'][0]['result'], {'value': 'must survive'})

    def test_explicit_removal_only_and_invalid_dependency_are_atomic(self):
        before = self.read()
        rejected = self.call(action='update', plan_id=self.pid, expected_version=self.version,
                             remove_step_ids=['second'])
        self.assertEqual(rejected.error_type, 'plan_validation_rejected', rejected)
        self.assertFalse(rejected.details.get('plan_write_outcome_unknown'))
        self.assertEqual(self.read(), before)
        removed = self.call(action='update', plan_id=self.pid, expected_version=self.version,
                            remove_step_ids=['third'])
        self.assertFalse(removed.is_error, removed)
        plan = self.read()
        self.assertEqual([step['id'] for step in plan['steps']], ['first', 'second'])
        self.assertEqual(plan['steps'][0]['result'], {'value': 'must survive'})

    def test_explicit_removal_can_reroute_dependency_in_same_transaction(self):
        rerouted = {**self.steps[2], 'depends_on': ['first']}
        result = self.call(action='update', plan_id=self.pid, expected_version=self.version,
                           steps=[rerouted], remove_step_ids=['second'])
        self.assertFalse(result.is_error, result)
        plan = self.read()
        self.assertEqual([step['id'] for step in plan['steps']], ['first', 'third'])
        self.assertEqual(plan['steps'][1]['depends_on'], ['first'])
        self.assertEqual(plan['steps'][0]['result'], {'value': 'must survive'})

    def test_unknown_production_cannot_be_deleted_replaced_or_added_under_new_id(self):
        with self.db.session() as session:
            row = session.get(AgentPlanStep, self.pid + ':first')
            row.tool = 'start_content_run'
            row.arguments_json = {'action': 'tasks', 'topic_id': 'review-1', 'tasks': [
                {'kind': 'image', 'title': '已提交', 'brief': '一张图'}]}
            row.wait_for_completion = True
            row.status, row.result_json = 'unknown', {'run_id': 'original-run'}
        before = self.read()
        production = {key: before['steps'][0][key] for key in
                      ('id', 'title', 'tool', 'arguments', 'depends_on', 'wait_for_completion')}
        modified = {**production, 'title': '不能覆盖已提交内容'}
        second = {**self.steps[1], 'depends_on': []}
        replacement = {**production, 'id': 'another', 'title': '换 ID 重复生产'}
        for args in ({'steps': [second], 'remove_step_ids': ['first']},
                     {'steps': [modified]}, {'steps': [replacement]}):
            with self.subTest(args=args):
                result = self.call(action='update', plan_id=self.pid, expected_version=self.version, **args)
                self.assertEqual(result.error_type, 'plan_edit_conflict', result)
                self.assertFalse(result.details.get('plan_write_outcome_unknown'))
                self.assertEqual(self.read(), before)
        web_delete = self.client.patch(f'{self.path}/{self.pid}', json={
            'expected_version': self.version, 'steps': [second, self.steps[2]]})
        self.assertEqual(web_delete.status_code, 409, web_delete.text)
        self.assertEqual(web_delete.json()['error']['code'], 'plan_edit_conflict')
        self.assertEqual(self.read(), before)
        self.assertEqual(self.producer.calls, 0)

    def test_edit_conflict_rolls_back_earlier_repair_mutations(self):
        with self.db.session() as session:
            first = session.get(AgentPlanStep, self.pid + ':first')
            first.tool = 'start_content_run'
            first.arguments_json = {'action': 'tasks', 'topic_id': 'review-1', 'tasks': [
                {'kind': 'image', 'title': '图', 'brief': '一张图'}]}
            first.wait_for_completion = True
            first.status, first.error = 'failed', '参数无效'
            first.result_json = {'_error_type': 'invalid_arguments', '_pre_dispatch': True}
            session.get(AgentPlanStep, self.pid + ':second').status = 'succeeded'
        before = self.read()
        first = {key: before['steps'][0][key] for key in
                 ('id', 'title', 'tool', 'arguments', 'depends_on', 'wait_for_completion')}
        repair = {**first, 'title': '修复参数'}
        blocked = {**self.steps[1], 'title': '非法改写已执行步骤'}
        rejected = self.call(action='update', plan_id=self.pid, expected_version=self.version,
                             steps=[repair, blocked])
        self.assertEqual(rejected.error_type, 'plan_edit_conflict', rejected)
        self.assertFalse(rejected.details.get('plan_write_outcome_unknown'))
        self.assertEqual(self.read(), before)
        self.assertEqual(self.producer.calls, 0)

    def test_http_merge_rejects_empty_and_ambiguous_removals_without_writes(self):
        before = self.read()
        for extra in ({'steps': [], 'remove_step_ids': []},
                      {'steps': [], 'remove_step_ids': ['not-existing']},
                      {'steps': [], 'remove_step_ids': ['third', 'third']},
                      {'steps': [self.steps[2]], 'remove_step_ids': ['third']}):
            with self.subTest(extra=extra):
                response = self.client.patch(f'{self.path}/{self.pid}', json={
                    'expected_version': self.version, 'mode': 'merge', **extra})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()['error']['code'], 'plan_validation_rejected')
                self.assertEqual(self.read(), before)
    def test_full_http_replace_still_removes_pending_step_and_stale_versions_do_not_write(self):
        response = self.client.patch(f'{self.path}/{self.pid}', json={
            'expected_version': self.version, 'steps': self.steps[:2]})
        self.assertEqual(response.status_code, 200, response.text)
        current = response.json()
        self.assertEqual([step['id'] for step in current['steps']], ['first', 'second'])
        stale = self.call(action='update', plan_id=self.pid, expected_version=self.version,
                          steps=[{**self.steps[1], 'title': 'stale'}])
        self.assertEqual(stale.error_type, 'plan_edit_conflict', stale)
        self.assertFalse(stale.details.get('plan_write_outcome_unknown'))
        self.assertEqual(self.read(), current)
        fresh = self.call(action='update', plan_id=self.pid, expected_version=current['version'],
                          steps=[{**self.steps[1], 'title': 'fresh'}])
        self.assertFalse(fresh.is_error, fresh)
        after = self.read()
        stale_web = self.client.patch(f'{self.path}/{self.pid}', json={
            'expected_version': current['version'], 'steps': self.steps[:2]})
        self.assertEqual(stale_web.status_code, 409, stale_web.text)
        self.assertEqual(stale_web.json()['error']['code'], 'plan_edit_conflict')
        self.assertEqual(self.read(), after)

    def test_continuation_partial_update_and_proven_rejection_have_same_semantics(self):
        self.context = replace(self.context, plan_continuation_id=self.pid)
        changed = {**self.steps[1], 'title': '续接修订'}
        result = self.call(action='update', plan_id=self.pid, expected_version=self.version, steps=[changed])
        self.assertFalse(result.is_error, result)
        current = self.read()
        self.assertEqual([item['id'] for item in current['steps']], ['first', 'second', 'third'])
        invalid = {**changed, 'depends_on': ['missing']}
        arguments = {'action': 'update', 'plan_id': self.pid, 'expected_version': current['version'], 'steps': [invalid]}
        call = ToolCall(str(uuid4()), 'manage_plan', json.dumps(arguments))
        denied = execute_tool_call(call, context=self.context, model_requested=True)
        self.assertEqual(denied.error_type, 'plan_validation_rejected', denied)
        self.assertEqual(denied.details['continuation_http_status'], 422)
        self.assertFalse(_unknown_continuation_write(call, denied, self.context))
        self.assertEqual(self.read(), current)

    def test_continuation_can_preserve_previously_authorized_unchanged_write(self):
        with self.db.session() as session:
            row = session.get(AgentPlanStep, self.pid + ':third')
            row.tool = 'manage_topics'
            row.arguments_json = {'action': 'create', 'series_id': 'agent-notes',
                                  'topics': [{'title': '用户此前授权的选题'}]}
        before = self.read()
        self.context = replace(self.context, plan_continuation_id=self.pid)
        changed = {**self.steps[1], 'title': '仅调整读取'}
        result = self.call(action='update', plan_id=self.pid, expected_version=self.version, steps=[changed])
        self.assertFalse(result.is_error, result)
        after = self.read()
        self.assertEqual(after['steps'][2], before['steps'][2])
        self.assertEqual(after['steps'][1]['title'], changed['title'])
        self.assertEqual(self.producer.calls, 0)
        write = {key: after['steps'][2][key] for key in
                 ('id', 'title', 'tool', 'arguments', 'depends_on', 'wait_for_completion')}
        modified = {**write, 'title': '自动续接试图修改写操作'}
        denied = self.call(action='update', plan_id=self.pid, expected_version=after['version'], steps=[modified])
        self.assertEqual(denied.error_type, 'agent_scope_rejected')
        self.assertEqual(self.read(), after)
        denied_removal = self.call(action='update', plan_id=self.pid, expected_version=after['version'],
                                   remove_step_ids=['third'])
        self.assertEqual(denied_removal.error_type, 'agent_scope_rejected')
        self.assertEqual(self.read(), after)

    def test_continuation_removal_checks_permission_and_started_state(self):
        self.context = replace(self.context, plan_continuation_id=self.pid)
        removed = self.call(action='update', plan_id=self.pid, expected_version=self.version,
                            remove_step_ids=['third'])
        self.assertFalse(removed.is_error, removed)
        with self.db.session() as session:
            row = session.get(AgentPlanStep, self.pid + ':first')
            row.tool = 'start_content_run'
            row.arguments_json = {'action': 'tasks', 'topic_id': 'review-1', 'tasks': [
                {'kind': 'image', 'title': '已提交', 'brief': '一张图'}]}
            row.wait_for_completion = True
            row.status, row.result_json = 'unknown', {'run_id': 'original-run'}
        before = self.read()
        second = {**self.steps[1], 'depends_on': []}
        rejected = self.call(action='update', plan_id=self.pid, expected_version=before['version'],
                             steps=[second], remove_step_ids=['first'])
        self.assertEqual(rejected.error_type, 'plan_conflict', rejected)
        self.assertFalse(rejected.details.get('continuation_write_started'))
        self.assertEqual(self.read(), before)
        self.assertEqual(self.producer.calls, 0)

    def test_explicit_removal_does_not_reset_omitted_blocked_step(self):
        with self.db.session() as session:
            row = session.get(AgentPlanStep, self.pid + ':second')
            row.status, row.error = 'blocked', '原有阻塞原因'
        result = self.call(action='update', plan_id=self.pid, expected_version=self.version,
                           remove_step_ids=['third'])
        self.assertFalse(result.is_error, result)
        plan = self.read()
        self.assertEqual(plan['steps'][1]['status'], 'blocked')
        self.assertEqual(plan['steps'][1]['error'], '原有阻塞原因')

    def test_omitted_failed_and_blocked_steps_keep_results_and_errors(self):
        with self.db.session() as session:
            blocked = session.get(AgentPlanStep, self.pid + ':second')
            blocked.status, blocked.error = 'blocked', '等待上游结果'
            failed = session.get(AgentPlanStep, self.pid + ':third')
            failed.status, failed.error, failed.result_json = 'failed', '原失败', {'original': 'evidence'}
        before = self.read()
        added = {'id': 'new', 'title': '新增读取', 'tool': 'get_producer_skill',
                 'arguments': {'skill_id': 'missing--0000000000000000'}, 'depends_on': []}
        result = self.call(action='update', plan_id=self.pid, expected_version=self.version, steps=[added])
        self.assertFalse(result.is_error, result)
        after = self.read()
        self.assertEqual(after['steps'][:3], before['steps'])
        self.assertEqual(after['steps'][3]['id'], 'new')

    def test_ordinary_chat_loop_updates_through_real_http(self):
        changed = {**self.steps[1], 'title': '聊天中修改'}
        provider = ScriptedProvider([[{'action': 'update', 'plan_id': self.pid,
                                      'expected_version': self.version, 'steps': [changed]}], []])
        self.app.state.chat.provider_factory = lambda: provider
        chat = self.app.state.chat
        view = chat.get(self.sid)
        chat.submit(self.sid, 'ordinary-real-http', '改一下计划第二步', view['version'], self.url)
        thread = chat.threads.get(self.sid)
        if thread:
            thread.join(25)
        self.assertNotIn(self.sid, chat.active)
        doc = chat._read(self.sid)
        self.assertEqual([entry['status'] for entry in doc['entries'] if entry['kind'] == 'tool'], ['done'])
        self.assertEqual(provider.calls, 2)
        plan = self.read()
        self.assertEqual([step['id'] for step in plan['steps']], ['first', 'second', 'third'])
        self.assertEqual(plan['steps'][1]['title'], changed['title'])
        self.assertEqual(plan['steps'][0]['result'], {'value': 'must survive'})

    def test_continuation_loop_can_correct_a_proven_rejection_without_replay(self):
        invalid = {**self.steps[1], 'depends_on': ['missing']}
        changed = {**self.steps[1], 'title': '修正后继续'}
        provider = ScriptedProvider([
            [{'action': 'update', 'plan_id': self.pid, 'expected_version': self.version, 'steps': [invalid]}],
            [{'action': 'update', 'plan_id': self.pid, 'expected_version': self.version, 'steps': [changed]}],
            [],
        ])
        chat = self.app.state.chat
        chat.provider_factory = lambda: provider
        with self.db.session() as session:
            row = session.get(AgentPlan, self.pid)
            row.continuation_state, row.continuation_version = 'pending', 1
        chat._resume_plans()
        thread = chat.threads.get(self.sid)
        if thread:
            thread.join(25)
        self.assertNotIn(self.sid, chat.active)
        doc = chat._read(self.sid)
        self.assertEqual([entry['status'] for entry in doc['entries'] if entry['kind'] == 'tool'], ['failed', 'done'])
        self.assertEqual(provider.calls, 3)
        plan = self.read()
        self.assertEqual(plan['steps'][1]['title'], changed['title'])
        self.assertEqual(plan['steps'][0]['result'], {'value': 'must survive'})
        self.assertEqual(plan['continuation_state'], 'done_checkpoint')
        self.assertEqual(self.producer.calls, 0)


class PlanIncrementalSchemaTests(unittest.TestCase):
    def test_canonical_definition_comparison_preserves_json_types_and_list_order(self):
        base = {'id': 'read', 'title': '读取', 'tool': 'manage_topics', 'arguments': {'count': 1},
                'depends_on': ['a', 'b'], 'wait_for_completion': False}
        self.assertTrue(_same_step_definition(base, dict(reversed(list(base.items())))))
        self.assertFalse(_same_step_definition(base, {**base, 'arguments': {'count': True}}))
        self.assertFalse(_same_step_definition(base, {**base, 'depends_on': ['b', 'a']}))

    def test_advertised_update_schema_matches_change_or_removal_requirement(self):
        tool = tool_registry['manage_plan']
        schema = tool.to_schema()['function']['parameters']
        branch = schema['$defs']['UpdatePlanArgs']
        base = {'action': 'update', 'plan_id': 'plan', 'expected_version': 1}
        step = {'id': 'read', 'title': '读取', 'tool': 'get_producer_skill',
                'arguments': {'skill_id': 'demo--0000000000000000'}}
        for value in ({**base, 'steps': [step]}, {**base, 'remove_step_ids': ['read']},
                      {**base, 'steps': [], 'remove_step_ids': ['read']}):
            with self.subTest(value=value):
                _validate_argument_shape(value, branch, schema)
                tool.parse_arguments(json.dumps(value))
        for value in (base, {**base, 'steps': [], 'remove_step_ids': []}):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    _validate_argument_shape(value, branch, schema)
                with self.assertRaises(ValueError):
                    tool.parse_arguments(json.dumps(value))


if __name__ == '__main__':
    unittest.main()
