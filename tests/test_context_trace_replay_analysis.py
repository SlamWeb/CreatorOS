import unittest

from scripts.context_trace_replay_analysis import _exact_repeat_stats


class TraceReplayAnalysisTests(unittest.TestCase):
    def test_exact_duplicates_only_and_no_content_in_result(self):
        private = "confidential-example"
        messages = [
            {"role": "assistant", "tool_calls": [
                {"id": "one", "name": "probe", "arguments": '{"id":"a"}'},
                {"id": "two", "name": "probe", "arguments": '{"id":"a"}'},
                {"id": "three", "name": "probe", "arguments": '{"id":"b"}'},
            ]},
            {"role": "tool", "content": private},
            {"role": "tool", "content": private},
            {"role": "tool", "content": private + " changed"},
        ]
        result = _exact_repeat_stats(messages)
        self.assertEqual(result["exact_duplicate_tool_result_messages"], 1)
        self.assertEqual(result["exact_duplicate_argument_calls"], 1)
        self.assertGreater(result["exact_duplicate_tool_result_estimate"], 0)
        self.assertNotIn(private, str(result))


if __name__ == "__main__":
    unittest.main()
