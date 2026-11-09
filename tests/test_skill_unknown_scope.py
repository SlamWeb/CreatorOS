"""Unknown Skill writes block the affected family, not unrelated Skills."""
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
from creatoros.ai.types import ToolCall
from creatoros.agent.loop import run_agent
from creatoros.agent.skill_write_guard import SkillUnknownWriteGuard
from creatoros.context import RuntimeContext
from creatoros.session.snapshot import load_messages
from creatoros.terminal import Console
from creatoros.tools.results import ToolResult
from tests.test_reconstruct_public_contract import ScriptedProvider
import tests.test_skill_version_scope as version_fixture


SOURCE = 'source--' + 'a' * 16
CANDIDATE = 'candidate--' + 'b' * 16
OTHER = 'other--' + 'c' * 16
FAMILY = 'source--' + 'd' * 16
OTHER_FAMILY = 'other--' + 'e' * 16
DIGEST = 'f' * 64


def write(skill_id, operation='write_text'):
    args = {'skill_id': skill_id, 'expected_digest': DIGEST, 'operation': operation}
    if operation == 'batch':
        args['changes'] = [{'operation': 'write_text', 'path': 'notes.md', 'content': 'update'}]
    else:
        args.update(path='notes.md', content='update')
    return args


class SkillUnknownScopeTests(unittest.TestCase):
    def run_case(self, protocol, operation, *, missing_family=False, raise_family=False, pre_dispatch=False):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            context = RuntimeContext(root, creator_id='account-a', agent_session_id='session-a',
                session_file=root / 'session.json', archive_only_reads=True,
                allowed_tools=frozenset({'update_producer_skill_file', 'get_producer_skill'}))
            def tool(identity, name, args):
                if protocol == 'discovery':
                    return (identity, 'call_discovered_tool', {'name': name, 'arguments': args})
                return (identity, name, args)
            batches = []
            if protocol == 'discovery':
                batches.append([('find', 'discover_tools', {'names': ['update_producer_skill_file', 'get_producer_skill']})])
            batches += [[tool('unknown', 'update_producer_skill_file', write(SOURCE, operation)),
                         tool('same', 'update_producer_skill_file', write(SOURCE)),
                         tool('related', 'update_producer_skill_file', write(CANDIDATE)),
                         tool('other', 'update_producer_skill_file', write(OTHER)),
                         tool('read', 'get_producer_skill', {'skill_id': SOURCE, 'list_files': True})], None]
            provider = ScriptedProvider(batches)
            executed, lookups = [], []
            def execute(call, **_):
                args = json.loads(call.arguments)
                executed.append((call.id, call.name, args))
                if call.id == 'unknown':
                    # A timeout can omit the new candidate identity entirely.
                    data = {'outcome_unknown': True, 'message': 'outcome unknown'}
                    return ToolResult(json.dumps(data), True, 'studio_outcome_unknown',
                                      details={'pre_dispatch': True} if pre_dispatch else {})
                return ToolResult('{"ok": true}')
            def versions(skill_id, **kwargs):
                lookups.append((skill_id, kwargs.get('context'), kwargs.get('include_versions')))
                if raise_family and skill_id == SOURCE:
                    raise RuntimeError('controlled unexpected family lookup failure')
                if missing_family and skill_id == SOURCE:
                    return ToolResult('unavailable', True, 'studio_request_failed')
                family = OTHER_FAMILY if skill_id == OTHER else FAMILY
                return ToolResult(json.dumps({'family_id': family, 'items': [{'id': skill_id, 'family_id': family}]}))
            env = {'CREATOROS_TOOL_PROTOCOL': protocol, 'CREATOROS_TOOL_FASTPATH': '1',
                   'CREATOROS_TOOL_SCHEMA_VERSION': '1'}
            if protocol == 'canonical':
                env['CREATOROS_TOOL_SCHEMA_VERSION'] = '2'
            inputs = iter(['请按要求编辑这些 Skill', '/exit'])
            with patch.dict(os.environ, env, clear=True), patch('creatoros.agent.loop.execute_tool_call', side_effect=execute), \
                 patch('creatoros.agent.skill_write_guard.get_producer_skill', side_effect=versions):
                run_agent(provider, session_file=context.session_file, runtime_context=context,
                          console=Console(input_fn=lambda _: next(inputs), output=StringIO()))
            ledger = load_messages(context.session_file)
            results = {m['tool_call_id']: m['content'] for m in ledger if m.get('role') == 'tool'}
            return executed, lookups, results

    def test_same_family_blocked_unrelated_and_reads_allowed_across_protocols(self):
        for protocol in ('legacy', 'discovery', 'canonical'):
            for operation in ('write_text', 'batch'):
                with self.subTest(protocol=protocol, operation=operation):
                    executed, lookups, results = self.run_case(protocol, operation)
                    self.assertEqual([x[0] for x in executed], ['unknown', 'other', 'read'])
                    self.assertIn('skill_edit_not_dispatched', results['same'])
                    self.assertIn('skill_edit_not_dispatched', results['related'])
                    self.assertNotIn('tool_error', results['other'])
                    self.assertNotIn('tool_error', results['read'])
                    self.assertTrue(all(context.creator_id == 'account-a' and versions
                                        for _, context, versions in lookups))

    def test_family_lookup_failure_blocks_when_relation_cannot_be_proved(self):
        executed, _, results = self.run_case('canonical', 'write_text', missing_family=True)
        self.assertEqual([x[0] for x in executed], ['unknown', 'read'])
        for call in ('same', 'related', 'other'):
            self.assertIn('skill_edit_not_dispatched', results[call])

    def test_unexpected_lookup_error_keeps_unknown_receipt_and_blocks_later_writes(self):
        executed, _, results = self.run_case('canonical', 'write_text', raise_family=True)
        self.assertEqual([x[0] for x in executed], ['unknown', 'read'])
        self.assertIn('studio_outcome_unknown', results['unknown'])
        for call in ('same', 'related', 'other'):
            self.assertIn('skill_edit_not_dispatched', results[call])

    def test_pre_dispatch_rejection_does_not_trigger_any_family_lookup(self):
        executed, lookups, results = self.run_case('canonical', 'write_text', pre_dispatch=True)
        self.assertEqual([x[0] for x in executed], ['unknown', 'same', 'related', 'other', 'read'])
        self.assertEqual(lookups, [])
        self.assertNotIn('tool_error', results['other'])

    def test_protection_resets_on_next_user_turn(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            context = RuntimeContext(root, creator_id='account-a', agent_session_id='session-a',
                session_file=root / 'session.json', archive_only_reads=True,
                allowed_tools=frozenset({'update_producer_skill_file'}))
            provider = ScriptedProvider([[('unknown', 'update_producer_skill_file', write(SOURCE))], None,
                                         [('next-turn', 'update_producer_skill_file', write(SOURCE))], None])
            executed, lookups = [], []
            def execute(call, **_):
                executed.append(call.id)
                if call.id == 'unknown':
                    return ToolResult('{"outcome_unknown": true}', True, 'studio_outcome_unknown')
                return ToolResult('{"ok": true}')
            def versions(skill_id, **_):
                lookups.append(skill_id)
                return ToolResult(json.dumps({'family_id': FAMILY, 'items': [{'id': skill_id, 'family_id': FAMILY}]}))
            inputs = iter(['第一轮修改', '第二轮重新处理', '/exit'])
            with patch.dict(os.environ, {'CREATOROS_TOOL_PROTOCOL': 'canonical'}, clear=True), \
                 patch('creatoros.agent.loop.execute_tool_call', side_effect=execute), \
                 patch('creatoros.agent.skill_write_guard.get_producer_skill', side_effect=versions):
                run_agent(provider, session_file=context.session_file, runtime_context=context,
                          console=Console(input_fn=lambda _: next(inputs), output=StringIO()))
            self.assertEqual(executed, ['unknown', 'next-turn'])
            self.assertEqual(lookups, [SOURCE])


class SkillUnknownRealScopeTests(unittest.TestCase):
    """Use an isolated local Studio server and SQLite; never call a producer."""
    setUp = version_fixture.SkillVersionScopeTests.setUp
    skill = version_fixture.SkillVersionScopeTests.skill
    header = version_fixture.SkillVersionScopeTests.header
    candidate = version_fixture.SkillVersionScopeTests.candidate

    def test_lost_reply_new_candidate_and_unrelated_skill_with_legacy_and_canonical(self):
        for protocol in ('legacy', 'canonical'):
            with self.subTest(protocol=protocol):
                source = self.catalog.read_skill_file(self.legacy['id'], 'SKILL.md')
                other = self.catalog.read_skill_file(self.mind['id'], 'SKILL.md')
                context = RuntimeContext(self.root, creator_id='review-lab', agent_session_id=self.account,
                    session_file=self.root / (uuid4().hex + '.json'), studio_url=self.url,
                    archive_only_reads=True, allowed_tools=frozenset({'update_producer_skill_file', 'get_producer_skill'}))
                source_args = {'skill_id': self.legacy['id'], 'path': 'SKILL.md',
                               'content': source['content'] + '\n未知回执的修改。\n', 'expected_digest': source['digest']}
                other_args = {'skill_id': self.mind['id'], 'path': 'SKILL.md',
                              'content': other['content'] + '\n独立 Skill 修改。\n', 'expected_digest': other['digest']}
                captured, writes, reads = {}, [], []
                original_request = httpx.Client.request
                def request(client, method, url, *args, **kwargs):
                    response = original_request(client, method, url, *args, **kwargs)
                    if method.upper() == 'GET' and str(url).endswith('/versions'):
                        reads.append((str(url), kwargs.get('headers', {}).get('x-creatoros-agent-session')))
                    if method.upper() == 'PUT' and '/files/content' in str(url):
                        writes.append(str(url))
                        if not captured:
                            self.assertEqual(response.status_code, 200, response.text)
                            captured.update(response.json())
                            raise httpx.ReadTimeout('controlled lost reply after isolated commit')
                    return response
                def batches():
                    yield [('unknown', 'update_producer_skill_file', source_args)]
                    yield [('read-versions', 'get_producer_skill', {'skill_id': self.legacy['id'], 'include_versions': True})]
                    yield [('related', 'update_producer_skill_file', {'skill_id': captured['skill_id'], 'path': 'SKILL.md',
                               'content': source['content'] + '\n不应再次写入。\n', 'expected_digest': captured['digest']}),
                           ('other', 'update_producer_skill_file', other_args)]
                    yield None
                inputs = iter(['请依次修改这两个 Skill', '/exit'])
                with patch.dict(os.environ, {'CREATOROS_TOOL_PROTOCOL': protocol}, clear=True), \
                     patch.object(httpx.Client, 'request', request):
                    run_agent(ScriptedProvider(batches()), session_file=context.session_file, runtime_context=context,
                              console=Console(input_fn=lambda _: next(inputs), output=StringIO()))
                ledger = load_messages(context.session_file)
                results = {m['tool_call_id']: m['content'] for m in ledger if m.get('role') == 'tool'}
                self.assertIn('studio_outcome_unknown', results['unknown'])
                self.assertIn(captured['skill_id'], results['read-versions'])
                self.assertIn('skill_edit_not_dispatched', results['related'])
                self.assertNotIn('tool_error', results['other'])
                self.assertEqual(len(writes), 2)
                self.assertNotEqual(captured['skill_id'], self.legacy['id'])
                self.assertTrue(reads)
                self.assertTrue(all(session == self.account for _, session in reads))
                self.assertEqual(self.producer.calls, 0)

    def test_family_lookup_does_not_bypass_session_scope(self):
        context = RuntimeContext(self.root, creator_id='review-lab', agent_session_id=self.other,
            session_file=self.root / 'different-account.json', studio_url=self.url, archive_only_reads=True)
        guard = SkillUnknownWriteGuard(context)
        args = {'skill_id': self.legacy['id'], 'path': 'SKILL.md', 'content': 'text', 'expected_digest': DIGEST}
        guard.record_unknown(ToolCall('unknown', 'update_producer_skill_file', json.dumps(args)))
        other_args = {'skill_id': self.mind['id'], 'path': 'SKILL.md', 'content': 'text', 'expected_digest': DIGEST}
        self.assertTrue(guard.blocks(ToolCall('other', 'update_producer_skill_file', json.dumps(other_args))))


if __name__ == '__main__':
    unittest.main()
