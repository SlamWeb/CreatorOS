"""Pure accounting normalization, not a replay or fake model response."""
import unittest
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
from creatoros.ai.context import estimate_tokens
from creatoros.evaluation.benchmark.capture import text_estimation_view


class BenchmarkTextEstimationTests(unittest.TestCase):
    def test_complete_trace_records_output_limit_and_finish_reason_not_private_reasoning(self):
        from creatoros.ai.context import ModelContext
        from creatoros.evaluation.benchmark.capture import MeasuredProvider
        records = []
        provider = MeasuredProvider(api_key="controlled-no-network-key", max_retries=0,
                                    reasoning_effort="high", sink=lambda record: records.append(deepcopy(record)))
        message = SimpleNamespace(content="", tool_calls=[], reasoning_content="private reasoning")
        result = SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="length")], usage=None)
        context = replace(ModelContext.from_messages([{"role": "user", "content": "test"}], []), max_output_tokens=8192)
        with patch.object(provider.client.chat.completions, "create", return_value=result):
            provider.complete(context)
        self.assertEqual(records[-1]["max_output_tokens"], 8192)
        self.assertEqual(records[-1]["actual_reasoning_effort"], "high")
        self.assertEqual(records[-1]["response"]["finish_reason"], "length")
        self.assertNotIn("private reasoning", str(records[-1]))
        provider.client.close()

    def test_image_encoding_size_cannot_masquerade_as_text_context_tokens(self):
        def sample(data):
            return [{'role': 'user', 'content': [{'type': 'text', 'text': '请评价这张图'},
                    {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + data}}]}]
        small, large = sample('AAAA'), sample('A' * 100000)
        self.assertEqual(estimate_tokens(text_estimation_view(small)), estimate_tokens(text_estimation_view(large)))
        self.assertEqual(large[0]['content'][1]['image_url']['url'], 'data:image/png;base64,' + 'A' * 100000)

    def test_normal_text_and_remote_reference_remain_part_of_estimate(self):
        original = {'text': '制作要求', 'url': 'https://example.test/reference.png', 'count': 3}
        self.assertEqual(text_estimation_view(original), original)
