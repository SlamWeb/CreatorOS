import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from creatoros.ai.context import ContextBudget, ModelContext
from creatoros.ai.types import ModelUsage
from creatoros.session.context_trace import ContextTrace, read_trace


class Provider:
    model = 'fixture-model'


def context(messages, tools=None):
    return ModelContext.from_messages(messages, tools or [{'function': {'name': 'lookup'}}])


def run(trace, ctx, turn='turn', provider=None, sent=True):
    with trace.request('main', provider or Provider(), turn_id=turn) as span:
        span.begin(ctx, ContextBudget.from_context(ctx))
        span.record['sent'] = sent
        span.usage(ModelUsage(100, 10, 110, 64, 36))
    return read_trace(trace.snapshots.session_file)['items'][-1]


class CachePrefixDiagnosticsTest(unittest.TestCase):
    def test_append_and_changed_account_schema_without_content_leak(self):
        with TemporaryDirectory() as directory:
            file = Path(directory) / 'messages.json'
            trace = ContextTrace(file, capture_payload=True)
            account = '[宿主提供的当前账号目录；secret account]'
            first = [{'role': 'system', 'content': 'sensitive system'},
                     {'role': 'user', 'content': account}, {'role': 'user', 'content': 'user private'}]
            row = run(trace, context(first))
            self.assertEqual(row['prefix_comparison']['reason'], 'no_previous_sent_main_in_trace_tail')
            longer = first + [{'role': 'assistant', 'content': 'private response',
                               'reasoning_content': 'private reasoning'}]
            row = run(trace, context(longer))
            compare = row['prefix_comparison']
            self.assertEqual(compare['messages_relation'], 'appended')
            self.assertEqual(compare['same_message_count'], 3)
            self.assertFalse(compare['tools_changed'])
            self.assertEqual(row['usage']['cache_hit_tokens'], 64)
            changed = [first[0], {'role': 'user', 'content': account + ' changed'}, *longer[2:]]
            row = run(trace, context(changed, [{'function': {'name': 'new_tool'}}]), turn='new-turn')
            compare = row['prefix_comparison']
            self.assertEqual(compare['messages_relation'], 'changed')
            self.assertEqual(compare['first_difference_categories'], ['account_context'])
            self.assertTrue(compare['tools_changed'])
            self.assertFalse(compare['same_turn'])
            self.assertEqual(compare['basis'], 'redacted_model_context_before_provider_conversion')
            rendered = json.dumps(compare)
            for secret in ('user private', 'private reasoning', 'secret account', 'sensitive system', 'new_tool'):
                self.assertNotIn(secret, rendered)

    def test_restart_uses_only_last_sent_main_and_isolation(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'a' / 'messages.json'
            trace = ContextTrace(path, capture_payload=True)
            original = context([{'role': 'user', 'content': 'first'}])
            previous = run(trace, original)
            run(trace, context([{'role': 'user', 'content': 'not sent'}]), sent=False)
            restarted = ContextTrace(path, capture_payload=True)
            row = run(restarted, original, turn='turn2')
            self.assertEqual(row['prefix_comparison']['previous_request_id'], previous['request_id'])
            self.assertEqual(row['prefix_comparison']['messages_relation'], 'unchanged')
            self.assertFalse(row['prefix_comparison']['same_turn'])
            other = ContextTrace(Path(directory) / 'b' / 'messages.json', capture_payload=True)
            self.assertEqual(run(other, original)['prefix_comparison']['status'], 'unavailable')

    def test_missing_old_snapshot_and_diagnostic_error_are_safe(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'messages.json'
            trace = ContextTrace(path, capture_payload=True)
            ctx = context([{'role': 'user', 'content': 'test'}])
            old = run(trace, ctx)
            trace.snapshots._path(old['request_id']).unlink()
            restarted = ContextTrace(path, capture_payload=True)
            row = run(restarted, ctx)
            self.assertEqual(row['prefix_comparison'], {'status': 'unavailable', 'reason': 'comparison_failed'})
            with patch('creatoros.session.context_trace.redact', side_effect=ValueError('private error')):
                # Call the diagnostic directly: errors yield a fixed public reason.
                self.assertEqual(restarted.compare_previous(ctx, 'fixture-model', 'turn'),
                                 {'status': 'unavailable', 'reason': 'comparison_failed'})

    def test_no_snapshots_and_compaction_do_not_invent_comparison(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'messages.json'
            trace = ContextTrace(path)
            ctx = context([{'role': 'user', 'content': 'test'}])
            with trace.request('main', Provider(), turn_id='a') as span:
                span.begin(ctx, ContextBudget.from_context(ctx))
                self.assertEqual(span.record['prefix_comparison']['reason'], 'snapshots_disabled')
            with trace.request('compaction', Provider(), turn_id='a') as span:
                span.begin(ctx, ContextBudget.from_context(ctx))
                self.assertNotIn('prefix_comparison', span.record)


if __name__ == '__main__':
    unittest.main()
