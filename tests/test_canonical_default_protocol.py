"""Default account tool protocol and explicit rollback compatibility."""
from copy import deepcopy
from io import StringIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from creatoros.agent.loop import run_agent
from creatoros.agent.tool_protocol import (CANONICAL_V2, DISCOVERY, DISCOVERY_PURE, DISCOVERY_V2,
    DISCOVERY_PURE_V2, EXPERIMENT_DIRECT, EXPERIMENT_HYBRID, select_tool_protocol)
from creatoros.context import RuntimeContext
from creatoros.terminal import Console
from creatoros.web.chat import ACCOUNT_TOOLS
from tests.test_reconstruct_public_contract import ScriptedProvider


class DefaultCanonicalProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(prefix='canonical-default-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.context = RuntimeContext(self.root, creator_id='account-a', agent_session_id='session-a',
            session_file=self.root / 'messages.json', archive_only_reads=True, allowed_tools=ACCOUNT_TOOLS)

    def select(self, environment):
        messages = [{'role': 'system', 'content': 'base'}]
        with patch.dict(os.environ, environment, clear=True):
            mode = select_tool_protocol(messages, self.context)
        return mode, messages

    def test_unconfigured_account_uses_canonical_36_tool_root(self):
        provider = ScriptedProvider([None])
        inputs = iter(('你好', '/exit'))
        with patch.dict(os.environ, {}, clear=True):
            run_agent(provider, session_file=self.context.session_file, runtime_context=self.context,
                      console=Console(input_fn=lambda _: next(inputs), output=StringIO()))
        messages, schemas = provider.requests[0]
        names = [item['function']['name'] for item in schemas]
        self.assertEqual(len(names), 36)
        self.assertIn('manage_topics', names)
        self.assertIn('start_content_run', names)
        self.assertNotIn('discover_tools', names)
        self.assertEqual(sum('直接使用已开放的业务工具' in str(m.get('content')) for m in messages), 1)

    def test_explicit_configuration_keeps_prior_meaning(self):
        cases = [
            ({'CREATOROS_TOOL_PROTOCOL': 'legacy'}, 'legacy'),
            ({'CREATOROS_TOOL_PROTOCOL': 'discovery'}, DISCOVERY),
            ({'CREATOROS_TOOL_PROTOCOL': 'discovery', 'CREATOROS_TOOL_SCHEMA_VERSION': '1'}, DISCOVERY),
            ({'CREATOROS_TOOL_PROTOCOL': 'discovery', 'CREATOROS_TOOL_SCHEMA_VERSION': '2'}, DISCOVERY_V2),
            ({'CREATOROS_TOOL_PROTOCOL': 'discovery', 'CREATOROS_TOOL_SCHEMA_VERSION': '1',
              'CREATOROS_TOOL_FASTPATH': '0'}, DISCOVERY_PURE),
            ({'CREATOROS_TOOL_PROTOCOL': 'discovery', 'CREATOROS_TOOL_SCHEMA_VERSION': '2',
              'CREATOROS_TOOL_FASTPATH': '0'}, DISCOVERY_PURE_V2),
            ({'CREATOROS_TOOL_PROTOCOL': 'canonical'}, CANONICAL_V2),
            ({'CREATOROS_TOOL_PROTOCOL': 'canonical', 'CREATOROS_TOOL_SCHEMA_VERSION': '2'}, CANONICAL_V2),
            ({'CREATOROS_TOOL_SCHEMA_VERSION': '1'}, DISCOVERY),
            ({'CREATOROS_TOOL_SCHEMA_VERSION': '2'}, DISCOVERY_V2),
            ({'CREATOROS_TOOL_FASTPATH': '0'}, DISCOVERY_PURE),
            ({'CREATOROS_TOOL_SCHEMA_VERSION': '2', 'CREATOROS_TOOL_ENTRY_EXPERIMENT': 'direct'}, EXPERIMENT_DIRECT),
            ({'CREATOROS_TOOL_PROTOCOL': 'discovery', 'CREATOROS_TOOL_SCHEMA_VERSION': '2',
              'CREATOROS_TOOL_ENTRY_EXPERIMENT': 'hybrid'}, EXPERIMENT_HYBRID),
            ({'CREATOROS_TOOL_ENTRY_EXPERIMENT': ''}, CANONICAL_V2),
        ]
        for environment, expected in cases:
            with self.subTest(environment=environment):
                self.assertEqual(self.select(environment)[0], expected)

    def test_invalid_new_session_configurations_are_rejected(self):
        cases = [
            {'CREATOROS_TOOL_PROTOCOL': 'canonical', 'CREATOROS_TOOL_SCHEMA_VERSION': '1'},
            {'CREATOROS_TOOL_PROTOCOL': 'canonical', 'CREATOROS_TOOL_SCHEMA_VERSION': '2',
             'CREATOROS_TOOL_ENTRY_EXPERIMENT': 'direct'},
            {'CREATOROS_TOOL_PROTOCOL': 'discovery', 'CREATOROS_TOOL_SCHEMA_VERSION': '1',
             'CREATOROS_TOOL_ENTRY_EXPERIMENT': 'hybrid'},
            {'CREATOROS_TOOL_ENTRY_EXPERIMENT': 'direct'},
            {'CREATOROS_TOOL_PROTOCOL': 'unknown'},
            {'CREATOROS_TOOL_SCHEMA_VERSION': 'unknown'},
        ]
        for environment in cases:
            with self.subTest(environment=environment), self.assertRaises(ValueError):
                self.select(environment)

    def test_old_pinned_and_unmarked_histories_are_not_migrated(self):
        environments = [
            {'CREATOROS_TOOL_PROTOCOL': 'discovery', 'CREATOROS_TOOL_SCHEMA_VERSION': '1'},
            {'CREATOROS_TOOL_PROTOCOL': 'discovery', 'CREATOROS_TOOL_SCHEMA_VERSION': '2'},
            {'CREATOROS_TOOL_PROTOCOL': 'discovery', 'CREATOROS_TOOL_SCHEMA_VERSION': '2',
             'CREATOROS_TOOL_ENTRY_EXPERIMENT': 'direct'},
            {'CREATOROS_TOOL_PROTOCOL': 'canonical', 'CREATOROS_TOOL_SCHEMA_VERSION': '2'},
        ]
        for environment in environments:
            with self.subTest(environment=environment):
                mode, history = self.select(environment)
                history.extend([{'role': 'user', 'content': '旧问题'}, {'role': 'assistant', 'content': '旧答案'}])
                before = deepcopy(history)
                with patch.dict(os.environ, {}, clear=True):
                    self.assertEqual(select_tool_protocol(history, self.context), mode)
                self.assertEqual(history, before)
        history = [{'role': 'system', 'content': 'base'}, {'role': 'user', 'content': '旧问题'}]
        before = deepcopy(history)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(select_tool_protocol(history, self.context), 'legacy')
        self.assertEqual(history, before)

    def test_non_account_cli_remains_legacy(self):
        context = RuntimeContext(self.root, session_file=self.root/'cli.json', archive_only_reads=False)
        messages = [{'role': 'system', 'content': 'base'}]
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(select_tool_protocol(messages, context), 'legacy')
        self.assertEqual(messages, [{'role': 'system', 'content': 'base'}])


if __name__ == '__main__':
    unittest.main()
