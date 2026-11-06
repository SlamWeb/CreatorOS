"""Freeze the tool suite's identity and parent-case reuse contract."""
import json
from pathlib import Path
import unittest

from creatoros.evaluation.benchmark.manifest import _dataset
from creatoros.evaluation.benchmark.nonvideo_guard import policy
from creatoros.evaluation.benchmark.suite_schema import validate_selection

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / 'docs/agent-eval/tool-calling-v1/cases.json'
PARENT = ROOT / 'docs/agent-eval/benchmark-v2/cases.json'


class ToolCallingV1Tests(unittest.TestCase):
    def test_subset_preserves_parent_tasks_and_scoring(self):
        suite = _dataset(SUITE)
        parent = json.loads(PARENT.read_text(encoding='utf-8'))
        self.assertEqual(suite['dataset_id'], 'creatoros-tool-calling-v1-20261107')
        self.assertEqual(suite['parent_dataset_id'], parent['dataset_id'])
        by_id = {case['id']: case for case in parent['cases']}
        self.assertEqual(len(suite['cases']), 12)
        for case in suite['cases']:
            self.assertEqual(case, by_id[case['id']], case['id'])
        self.assertEqual(suite['suite']['eligible_case_ids'], [case['id'] for case in suite['cases']])

    def test_selection_and_guard_do_not_widen_automatically(self):
        suite = _dataset(SUITE)
        validate_selection(suite, ['B05', 'B06', 'B10'])
        for selection in (['B05', 'B05'], ['B04'], []):
            with self.assertRaises(ValueError):
                validate_selection(suite, selection)
        self.assertEqual(policy(['B05', 'B06', 'B10'])['video_dispatch'], 'deny')
        with self.assertRaises(ValueError):
            policy(['B31'])


if __name__ == '__main__':
    unittest.main()
