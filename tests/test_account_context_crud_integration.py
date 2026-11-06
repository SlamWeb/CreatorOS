import json
from io import StringIO
from pathlib import Path
import unittest
from unittest.mock import patch

from creatoros.agent.account_context_history import EVENT_SOURCE, replay_account_context
from creatoros.agent.loop import run_agent
from creatoros.ai.types import StreamEnd, TextDelta, ToolCallDelta
from creatoros.context import RuntimeContext
from creatoros.session.snapshot import load_messages, save_messages
from creatoros.storage import Creator, Series
from creatoros.terminal import Console
from creatoros.web.account_context import CreatorContextBuilder
from creatoros.web.chat import ACCOUNT_TOOLS
from tests import test_skill_version_scope as scope_fixture


class ScriptedProvider:
    context_window = 1000000
    reserve_output_tokens = 4096

    def __init__(self, calls=()):
        self.calls = calls
        self.contexts = []

    def stream(self, context):
        self.contexts.append(context.to_request()[0])
        if self.calls and len(self.contexts) == 1:
            for index, (name, args) in enumerate(self.calls):
                yield ToolCallDelta(index, f'call-{index}', name, json.dumps(args))
            yield StreamEnd('tool_calls')
        else:
            yield TextDelta('done')
            yield StreamEnd('stop')


class AccountCrudIntegration(unittest.TestCase):
    # Reuse the actual isolated database, HTTP server, account sessions and skills.
    setUp = scope_fixture.SkillVersionScopeTests.setUp
    skill = scope_fixture.SkillVersionScopeTests.skill
    header = scope_fixture.SkillVersionScopeTests.header
    candidate = scope_fixture.SkillVersionScopeTests.candidate

    def query(self, label, calls=()):
        provider = ScriptedProvider(calls)
        prompts = iter([label, '/exit'])
        path = self.root / 'account-ledger.json'
        if not path.exists():
            save_messages([{'role': 'system', 'content': 'system'}], path)
        # This fixture scripts the pre-existing canonical tool protocol; the
        # gateway's canonical dispatch is exercised in its own integration test.
        with patch.dict('os.environ', {'CREATOROS_TOOL_PROTOCOL': 'legacy'}):
            run_agent(provider, session_file=path, require_existing_session=True,
                      runtime_context=RuntimeContext(self.root, studio_url=self.url,
                          creator_id='review-lab', agent_session_id=self.account,
                          allowed_tools=ACCOUNT_TOOLS, archive_only_reads=True),
                      context_factory=self.snapshot,
                      console=Console(input_fn=lambda _: next(prompts), output=StringIO()))
        return provider.contexts, load_messages(path)

    def snapshot(self):
        return CreatorContextBuilder(self.database, self.catalog, self.app.state.skill_library).build('review-lab')

    def assert_current(self, ledger):
        self.assertEqual(replay_account_context(ledger), self.snapshot())
        self.assertNotIn('other-series', json.dumps(replay_account_context(ledger)))
        self.assertEqual(self.producer.calls, 0)

    def test_real_tool_batch_failure_scope_and_restart(self):
        self.app.state.skill_library.add('review-lab', self.mind['id'], 'setup-mind')
        calls = [('manage_skill_library', {'action': 'set_pool', 'series_id': 'agent-notes',
                    'expected_version': 0, 'skill_ids': [self.mind['id']]}),
                 ('manage_skill_library', {'action': 'get', 'series_id': 'agent-notes'})]
        contexts, ledger = self.query('change the pool', calls)
        self.assertEqual(contexts[1][:len(contexts[0])], contexts[0])
        self.assert_current(ledger)
        events = [i for i, item in enumerate(ledger) if item.get('creatoros_source') == EVENT_SOURCE]
        self.assertEqual(len(events), 2)
        self.assertEqual([m['role'] for m in ledger[events[0]+1:events[1]]], ['assistant', 'tool', 'tool'])
        old_ledger = list(ledger)
        _, ledger = self.query('read after restart')
        self.assertEqual(ledger[:len(old_ledger)], old_ledger)
        self.assertEqual(sum(m.get('creatoros_source') == EVENT_SOURCE for m in ledger), 2)
        self.assert_current(ledger)
        before = self.snapshot()
        _, ledger = self.query('stale or foreign changes', [
            ('manage_skill_library', {'action': 'set_pool', 'series_id': 'agent-notes',
                'expected_version': 0, 'skill_ids': []}),
            ('manage_skill_library', {'action': 'get', 'series_id': 'other-series'})])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(sum(m.get('creatoros_source') == EVENT_SOURCE for m in ledger), 2)
        self.assert_current(ledger)
        tool_results = [m['content'] for m in ledger[len(old_ledger):] if m['role'] == 'tool']
        self.assertEqual(len(tool_results), 2)
        self.assertTrue(all('error' in str(result).lower() for result in tool_results), tool_results)

    def test_real_database_changes_append_without_rewriting_history(self):
        contexts, ledger = self.query('initial')
        self.assert_current(ledger)
        previous = ledger
        mutations = [
            lambda db: setattr(db.get(Creator, 'review-lab'), 'display_name', '重命名账号'),
            lambda db: setattr(db.get(Series, 'agent-notes'), 'name', '重命名栏目'),
            lambda db: setattr(db.get(Series, 'agent-notes'), 'skill_name', self.mind['id']),
            lambda db: setattr(db.get(Series, 'agent-notes'), 'is_active', False),
        ]
        for index, mutate in enumerate(mutations):
            with self.database.session() as db:
                mutate(db)
            _, ledger = self.query(f'after change {index}')
            self.assertEqual(ledger[:len(previous)], previous)
            self.assertEqual(sum(m.get('creatoros_source') == EVENT_SOURCE for m in ledger), index + 2)
            self.assert_current(ledger)
            previous = ledger
        with self.database.session() as db:
            db.get(Series, 'other-series').name = '跨账号变化'
        _, ledger = self.query('other account changed')
        self.assertEqual(sum(m.get('creatoros_source') == EVENT_SOURCE for m in ledger), 5)
        self.assert_current(ledger)


    def test_candidate_skill_metadata_edit_is_appended(self):
        candidate = self.candidate(self.legacy)
        skill_id = candidate['skill_id']
        self.app.state.skill_library.add('review-lab', skill_id, 'setup-candidate')
        _, first = self.query('read skill')
        before = self.snapshot()
        source = self.catalog.read_skill_file(skill_id, 'SKILL.md')
        content = source['content'].replace('description: test complete', 'description: changed metadata')
        self.assertNotEqual(content, source['content'])
        response = self.client.put(f'/api/producer-skills/{skill_id}/files/content', headers=self.header(),
            json={'path': 'SKILL.md', 'content': content, 'expected_digest': source['digest']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotEqual(before, self.snapshot())
        _, after = self.query('read updated skill')
        self.assertEqual(after[:len(first)], first)
        self.assertEqual(sum(m.get('creatoros_source') == EVENT_SOURCE for m in after), 2)
        self.assert_current(after)


if __name__ == '__main__':
    unittest.main()
