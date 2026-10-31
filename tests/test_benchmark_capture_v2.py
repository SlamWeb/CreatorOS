"""Pure accounting normalization, not a replay or fake model response."""
import unittest
from creatoros.ai.context import estimate_tokens
from creatoros.evaluation.benchmark.capture import text_estimation_view


class BenchmarkTextEstimationTests(unittest.TestCase):
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
