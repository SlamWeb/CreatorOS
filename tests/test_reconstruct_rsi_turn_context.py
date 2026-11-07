"""Real Web turn and DeepSeek wire projection, scripted provider, no model API."""
from copy import deepcopy
import json
import os
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
from time import monotonic, sleep

import httpx

from creatoros.ai.deepseek import DeepSeekProvider
from creatoros.ai.types import StreamEnd, TextDelta, ToolCallDelta
from creatoros.session.snapshot import load_messages, save_messages
from creatoros.web.chat import AgentChatService
from tests import test_reconstruct_skill_iteration_start as old
from tests.smoke_web_agent import wait_idle
from tests.test_reconstruct_public_contract import env


OLD_RSI_SUFFIX = ('\n宿主确认：本条用户请求开启RSI，仅授权对其明确目标进行最多3轮候选试产；'
    '不继承到后续请求，不授权发布或栏目切版。目标不明确仍应先澄清。'
    '目标明确时通过manage_skill_iteration启动持久任务，不以仅编辑Skill代替迭代。'
    '任务自己提交试产并等待结果；以active_round.production_submitted和production_status核对，'
    '不可因普通Run列表为空而另开生产。评审失败只恢复同阶段，不重新生图。'
    '未收到实际创建回执时不能声称已启动或会自动继续。')


class Recorder:
    context_window = 1_000_000
    reserve_output_tokens = 4096
    def __init__(self, capture, turn, tools=False):
        self.capture, self.turn, self.tools, self.count = capture, turn, tools, 0
        self.client = SimpleNamespace(close=lambda: None)

    def stream(self, context):
        messages, tools = context.to_request()
        projection = object.__new__(DeepSeekProvider)
        projection.reasoning_effort = 'high'
        projection.image_resolver = None
        self.capture.append({'turn': self.turn, 'step': self.count,
            'wire_messages': deepcopy(projection._to_openai_messages(messages)), 'wire_tools': deepcopy(tools),
            'ledger_view': deepcopy(messages)})
        self.count += 1
        if self.tools and self.count == 1:
            yield ToolCallDelta(0, 'find-plan', 'discover_tools', json.dumps({'names': ['manage_plan.list']}))
            yield StreamEnd('tool_calls')
        elif self.tools and self.count == 2:
            yield ToolCallDelta(0, 'list-plan', 'call_discovered_tool', json.dumps({'name': 'manage_plan.list', 'arguments': {}}))
            yield StreamEnd('tool_calls')
        else:
            yield TextDelta('收到。')
            yield StreamEnd('stop')


def host_grants(messages):
    """Only host-marked per-turn system events, excluding the base prompt."""
    return [m for m in messages[1:] if m.get('role') in ('system', 'developer')
            and isinstance(m.get('creatoros_source'), str)
            and 'RSI' in str(m.get('content', ''))]


class RsiTurnContextTests(unittest.TestCase):
    setUp = old.RsiStartHttpTests.setUp
    assert_no_worker = old.RsiStartHttpTests.assert_no_worker

    def persist_evidence(self, name, payload):
        target = os.environ.get('CREATOROS_RSI_TURN_EVIDENCE')
        if target:
            path = Path(target)
            path.mkdir(parents=True, exist_ok=True)
            (path / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    def test_web_false_true_false_preserves_wire_prefix_and_explicit_text(self):
        """TR-RSI-TURN-01: mode belongs to one turn; discovery loops never duplicate it."""
        captures = []
        providers = iter([Recorder(captures, 'ordinary'), Recorder(captures, 'rsi', True),
                          Recorder(captures, 'after'), Recorder(captures, 'explicit')])
        self.app.state.chat.provider_factory = lambda: next(providers)
        requests = [('ordinary', False, '你好。'), ('rsi', True, '请按我选定的目标处理。'),
                    ('after', False, '谢谢，只是聊聊。'), ('explicit', False, '请开始本次 RSI 迭代。')]
        turns = []
        with httpx.Client(base_url=self.url, timeout=15, trust_env=False) as client, patch.dict('os.environ', env('2')):
            for label, mode, text in requests:
                rid = str(uuid4())
                version = self.app.state.chat.get(self.sid)['version']
                response = client.post(f'/api/agent/sessions/{self.sid}/turns', json={'request_id': rid,
                    'text': text, 'iteration_mode': mode, 'expected_version': version})
                self.assertEqual(response.status_code, 202, response.text)
                done = wait_idle(client, self.sid)
                self.assertEqual(done['status'], 'idle', done)
                ledger = load_messages(self.app.state.chat._path(self.sid).with_name('messages.json'))
                turns.append({'label': label, 'request_id': rid, 'ledger': ledger})
        self.persist_evidence('web-wire.json', {'requests': requests, 'turns': turns, 'provider': captures,
            'provider_usage': None, 'actual_billing': None, 'note': 'scripted provider; converted wire only, no network model'})
        groups = {label: [x for x in captures if x['turn'] == label] for label, _, _ in requests}
        self.assertEqual([len(groups[k]) for k, _, _ in requests], [1, 3, 1, 1])
        headers = [groups[k][0]['wire_messages'][0] for k, _, _ in requests]
        self.assertTrue(all(x == headers[0] for x in headers), 'RSI mode changed the provider system prefix')
        self.assertTrue(all(groups[k][0]['wire_tools'] == groups['ordinary'][0]['wire_tools'] for k, _, _ in requests))
        for earlier, later in [('ordinary', 'rsi'), ('rsi', 'after'), ('after', 'explicit')]:
            previous = groups[earlier][-1]['wire_messages']
            current = groups[later][0]['wire_messages']
            self.assertEqual(current[:len(previous)], previous, f'earlier wire prefix changed: {earlier} -> {later}')
        grant_counts = [len(host_grants(groups[k][0]['ledger_view'])) for k, _, _ in requests]
        self.assertEqual(grant_counts, [0, 1, 1, 1])
        for record in groups['rsi']:
            self.assertEqual(len(host_grants(record['ledger_view'])), 1)
        self.assertIn('请开始本次 RSI 迭代。', str(groups['explicit'][0]['wire_messages']))
        self.assert_no_worker()

    def test_legacy_mode_suffix_migrates_once_without_losing_history(self):
        """TR-RSI-TURN-02: old true-mode header is not permanent authorization."""
        ledger_file = self.app.state.chat._path(self.sid).with_name('messages.json')
        original = load_messages(ledger_file)
        original[0]['content'] += OLD_RSI_SUFFIX
        original += [{'role': 'user', 'content': '旧轮消息'}, {'role': 'assistant', 'content': '旧轮回答'}]
        save_messages(original, ledger_file)
        captures = []
        providers = iter([Recorder(captures, 'restore'), Recorder(captures, 'next')])
        self.app.state.chat.provider_factory = lambda: next(providers)
        snapshots = []
        with httpx.Client(base_url=self.url, timeout=15, trust_env=False) as client, patch.dict('os.environ', env('2')):
            for label in ('restore', 'next'):
                response = client.post(f'/api/agent/sessions/{self.sid}/turns', json={'request_id': str(uuid4()),
                    'text': '现在只聊聊。', 'iteration_mode': False,
                    'expected_version': self.app.state.chat.get(self.sid)['version']})
                self.assertEqual(response.status_code, 202, response.text)
                done = wait_idle(client, self.sid)
                self.assertEqual(done['status'], 'idle', done)
                snapshots.append(load_messages(ledger_file))
        self.persist_evidence('legacy-migration.json', {'before': original, 'after': snapshots, 'provider': captures})
        self.assertNotIn(OLD_RSI_SUFFIX, snapshots[0][0]['content'])
        self.assertEqual(snapshots[0][1:3], original[1:3])
        self.assertEqual(snapshots[1][0], snapshots[0][0])
        self.assertEqual(snapshots[1][:len(snapshots[0])], snapshots[0])
        self.assertEqual(len(host_grants(captures[0]['ledger_view'])), 0)
        self.assertEqual(len(host_grants(captures[1]['ledger_view'])), 0)
        self.assert_no_worker()

    def test_recreated_host_marks_interrupted_without_replaying_prior_grant(self):
        """TR-RSI-TURN-03: host reconstruction, not an OS process restart."""
        captures = []
        self.app.state.chat.provider_factory = lambda: Recorder(captures, 'first')
        old_id = str(uuid4())
        with httpx.Client(base_url=self.url, timeout=15, trust_env=False) as client, patch.dict('os.environ', env('2')):
            payload = {'request_id': old_id, 'text': '处理当前目标。', 'iteration_mode': True,
                       'expected_version': self.app.state.chat.get(self.sid)['version']}
            response = client.post(f'/api/agent/sessions/{self.sid}/turns', json=payload)
            self.assertEqual(response.status_code, 202, response.text)
            self.assertEqual(wait_idle(client, self.sid)['status'], 'idle')
        prior = self.app.state.chat._read(self.sid)
        prior['status'] = 'running'
        prior['requests'][-1]['status'] = 'running'
        self.app.state.chat._save(prior)
        ledger_path = self.app.state.chat._path(self.sid).with_name('messages.json')
        ledger = load_messages(ledger_path)
        ledger.append({'role': 'assistant', 'content': '', 'tool_calls': [
            {'id': 'interrupted-call', 'name': 'call_discovered_tool', 'arguments': '{}'}]})
        save_messages(ledger, ledger_path)
        host = self.app.state.chat
        restarted = AgentChatService(host.root, provider_factory=lambda: Recorder(captures, 'after-restart'),
            creator_lookup=host.creator_lookup, creator_context_factory=host.creator_context_factory,
            focus_context_builder=host.focus_context_builder)
        restarted.plan_service = host.plan_service
        self.addCleanup(restarted.shutdown)
        with patch.dict('os.environ', env('2')):
            restarted.start()
            recovered = restarted.get(self.sid)
            self.assertEqual(recovered['status'], 'interrupted')
            repaired = load_messages(ledger_path)
            missing = [x for x in repaired if x.get('tool_call_id') == 'interrupted-call']
            self.assertEqual(len(missing), 1)
            self.assertIn('未知', missing[0]['content'])
            self.assertEqual(len(captures), 1, 'recovery must not call the provider')
            restarted.submit(self.sid, old_id, payload['text'], payload['expected_version'], self.url, iteration_mode=True)
            self.assertEqual(len(captures), 1, 'same request replay must not restart the old turn')
            restarted.submit(self.sid, str(uuid4()), '只是闲聊。', recovered['version'], self.url, iteration_mode=False)
            deadline = monotonic() + 20
            while monotonic() < deadline and restarted.get(self.sid)['status'] == 'running':
                sleep(.05)
            current = restarted.get(self.sid)
            final_ledger = load_messages(ledger_path)
        self.persist_evidence('host-recovery.json', {'before_interruption': ledger,
            'after_repair': repaired, 'after_next_turn': final_ledger, 'provider': captures})
        self.assertEqual(current['status'], 'idle', current)
        self.assertEqual(final_ledger[:len(repaired)], repaired)
        self.assertEqual(len(captures), 2)
        self.assertEqual(len(host_grants(captures[0]['ledger_view'])), 1)
        self.assertEqual(len(host_grants(captures[1]['ledger_view'])), 1)
        self.assert_no_worker()

    def test_real_system_configuration_change_invalidates_once_then_stabilizes(self):
        """TR-RSI-TURN-04: an actual policy update is not hidden for cache reuse."""
        import creatoros.web.chat as chat
        captures = []
        providers = iter([Recorder(captures, 'before'), Recorder(captures, 'changed'), Recorder(captures, 'stable')])
        self.app.state.chat.provider_factory = lambda: next(providers)
        replacement = chat.ACCOUNT_INSTRUCTIONS + '\n隔离测试：新的系统规则版本。'
        snapshots = []
        with httpx.Client(base_url=self.url, timeout=15, trust_env=False) as client, patch.dict('os.environ', env('2')):
            for index, label in enumerate(('before', 'changed', 'stable')):
                with patch.object(chat, 'ACCOUNT_INSTRUCTIONS', replacement if index else chat.ACCOUNT_INSTRUCTIONS):
                    response = client.post(f'/api/agent/sessions/{self.sid}/turns', json={
                        'request_id': str(uuid4()), 'text': '你好。', 'iteration_mode': False,
                        'expected_version': self.app.state.chat.get(self.sid)['version']})
                    self.assertEqual(response.status_code, 202, response.text)
                    self.assertEqual(wait_idle(client, self.sid)['status'], 'idle')
                    snapshots.append(load_messages(self.app.state.chat._path(self.sid).with_name('messages.json')))
        self.persist_evidence('config-change.json', {'snapshots': snapshots, 'provider': captures})
        first, changed, stable = [x['wire_messages'][0]['content'] for x in captures]
        self.assertNotEqual(first, changed)
        self.assertIn('新的系统规则版本', changed)
        self.assertEqual(changed, stable)
        self.assertNotIn(OLD_RSI_SUFFIX, changed)
        self.assertEqual(snapshots[1][1:len(snapshots[0])], snapshots[0][1:])
        self.assertEqual(snapshots[2][:len(snapshots[1])], snapshots[1])
        self.assert_no_worker()
