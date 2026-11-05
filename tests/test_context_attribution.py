import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from creatoros.ai.context import ContextBudget, ModelContext
from creatoros.session.context_trace import ContextTrace, breakdown, detailed_breakdown, read_trace


class ContextAttributionTest(unittest.TestCase):
    def test_mutually_exclusive_estimates_and_tool_pairing(self):
        context = ModelContext.from_messages([
            {'role': 'system', 'content': 'system'},
            {'role': 'user', 'content': 'question'},
            {'role': 'assistant', 'content': 'answer', 'reasoning_content': 'private reasoning 中' * 10,
             'tool_calls': [{'id': 'reused', 'name': 'first', 'arguments': '{"query":"secret"}'},
                            {'id': 'second', 'function': {'name': 'second', 'arguments': '{"x":1}'}}]},
            {'role': 'tool', 'tool_call_id': 'second', 'content': 'second result'},
            {'role': 'tool', 'tool_call_id': 'reused', 'content': 'first result'},
            {'role': 'assistant', 'tool_calls': [{'id': 'reused', 'name': 'second', 'arguments': '{}'}]},
            {'role': 'tool', 'tool_call_id': 'reused', 'content': 'later result'},
            {'role': 'tool', 'tool_call_id': 'missing', 'content': 'unmatched'},
            {'role': 'assistant', 'tool_calls': [{'id': 'dup', 'name': 'first', 'arguments': '{}'},
                                                {'id': 'dup', 'name': 'second', 'arguments': '{}'}]},
            {'role': 'tool', 'tool_call_id': 'dup', 'content': 'ambiguous'},
        ], [{'function': {'name': 'first'}}, {'function': {'name': 'second'}}])
        original = context.to_request()
        legacy = breakdown(context, kind='main')
        detail = detailed_breakdown(context, kind='main', parts=legacy)
        parts, tools = detail['parts'], detail['tool_results_by_tool']
        self.assertEqual(context.to_request(), original)
        self.assertEqual(sum(parts.values()), sum(legacy.values()))
        self.assertEqual(sum(parts.values()), ContextBudget.from_context(context).estimated_input_tokens)
        self.assertTrue(all(value >= 0 for value in parts.values()))
        self.assertGreater(parts['history_reasoning'], 0)
        self.assertGreater(parts['tool_arguments'], 0)
        self.assertEqual(parts['recent_messages'] + parts['history_reasoning'] + parts['tool_arguments'],
                         legacy['recent_messages'])
        self.assertEqual(sum(tools.values()), parts['tool_results'])
        self.assertEqual(set(tools), {'first', 'second', 'unknown'})
        self.assertNotIn('private reasoning', json.dumps(detail))
        self.assertNotIn('secret', json.dumps(detail))

    def test_summary_does_not_claim_main_history_components(self):
        context = ModelContext.from_messages([{'role': 'user', 'content': 'summary source'}], [])
        detail = detailed_breakdown(context, kind='compaction')
        self.assertGreater(detail['parts']['summary_source'], 0)
        self.assertEqual(detail['parts']['history_reasoning'], 0)
        self.assertEqual(detail['parts']['tool_arguments'], 0)
        self.assertEqual(detail['tool_results_by_tool'], {})

    def test_trace_keeps_legacy_fields_and_adds_detail(self):
        context = ModelContext.from_messages([
            {'role': 'user', 'content': 'question'},
            {'role': 'assistant', 'reasoning_content': 'private',
             'tool_calls': [{'id': 'id', 'name': 'lookup', 'arguments': '{"secret":1}'}]},
            {'role': 'tool', 'tool_call_id': 'id', 'content': 'private result'},
        ], [{'function': {'name': 'lookup'}}])
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'messages.json'
            with ContextTrace(path).request('main', object(), turn_id='turn') as span:
                span.begin(context, ContextBudget.from_context(context))
            row = read_trace(path)['items'][-1]
            self.assertEqual(sum(row['estimated_parts'].values()), row['estimated_input_tokens'])
            self.assertEqual(sum(row['estimated_detail']['parts'].values()), row['estimated_input_tokens'])
            self.assertEqual(sum(row['estimated_detail']['tool_results_by_tool'].values()),
                             row['estimated_parts']['tool_results'])
            self.assertNotIn('private', json.dumps(row))
            self.assertNotIn('secret', json.dumps(row))


if __name__ == '__main__':
    unittest.main()
