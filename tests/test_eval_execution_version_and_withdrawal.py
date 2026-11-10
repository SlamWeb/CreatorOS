"""No paid call may start with an unbound code identity or a withdrawn case."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from creatoros.evaluation import tool_calling
from creatoros.evaluation.registry import EvalRegistry
from creatoros.evaluation.benchmark import run as benchmark_run


class EvaluationVersionAndWithdrawalTests(unittest.TestCase):
    def test_unverified_version_never_starts_model(self):
        sha = 'a' * 40
        scenarios = (
            ('different-sha', {'sha': 'b' * 40, 'dirty': False, 'status_available': True}, sha),
            ('dirty', {'sha': sha, 'dirty': True, 'status_available': True}, sha),
            ('unknown-status', {'sha': sha, 'dirty': None, 'status_available': False}, sha),
            ('abbreviated-sha', {'sha': sha, 'dirty': False, 'status_available': True}, sha[:7]),
        )
        for name, git, claimed in scenarios:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                batch = root / 'data/agent-benchmark-v2/run01'
                registry = EvalRegistry(root / 'registry.sqlite')
                calls = []
                def invoke(args):
                    calls.append(args[3])
                    if args[3] == 'freeze':
                        batch.mkdir(parents=True)
                        raw = json.dumps({'git': git, 'model_configuration': {'catalog_binding': None}}).encode()
                        (batch / 'manifest.json').write_bytes(raw)
                        (batch / 'manifest.json.sha256').write_text(tool_calling._sha(raw), encoding='ascii')
                    return 0
                dataset = {'dataset_id': tool_calling.SUITE_ID, 'cases': [{'id': 'B05'}]}
                with patch.object(tool_calling, 'ROOT', root), \
                     patch.object(tool_calling, 'suite_and_metadata', return_value=(dataset, {})):
                    with self.assertRaisesRegex(ValueError, 'clean frozen Git SHA'):
                        tool_calling.run_registered(registry, round_data={'id':'round', 'title':'test', 'code_version':claimed},
                            run_id='run01', cases=['B05'], allow_paid=True, invoke=invoke)
                self.assertEqual(calls, ['freeze'])
                run = registry.get_run('run01')
                self.assertEqual(run['status'], 'failed')
                self.assertIsNone(run['manifest_sha256'])

    def test_withdrawn_e06_cannot_freeze_but_revision_and_other_cases_can(self):
        root = Path(__file__).resolve().parents[1]
        old = root / 'docs/agent-eval/expansion-50-v1/cases.json'
        revised = root / 'docs/agent-eval/e06-scope-v2/cases.json'
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            with self.assertRaisesRegex(ValueError, 'Withdrawn expansion E06'):
                benchmark_run.freeze(batch, old, ['E06'])
            self.assertFalse((batch / 'manifest.json').exists())
        benchmark_run._reject_withdrawn_e06(old, ['E07'])
        benchmark_run._reject_withdrawn_e06(revised, ['E06'])
        with self.assertRaisesRegex(ValueError, 'Withdrawn expansion E06'):
            benchmark_run._reject_withdrawn_e06(old, None)

    def test_repackaged_old_case_or_rubric_is_still_withdrawn(self):
        root = Path(__file__).resolve().parents[1]
        old = json.loads((root / 'docs/agent-eval/expansion-50-v1/cases.json').read_text(encoding='utf8'))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'repackaged.json'
            old['dataset_id'] = 'another-dataset'
            path.write_text(json.dumps(old, ensure_ascii=False), encoding='utf8')
            with self.assertRaisesRegex(ValueError, 'Withdrawn expansion E06'):
                benchmark_run._reject_withdrawn_e06(path, ['E06'])
            next(item for item in old['cases'] if item['id'] != 'E06')['title'] += ' modified'
            path.write_text(json.dumps(old, ensure_ascii=False), encoding='utf8')
            with self.assertRaisesRegex(ValueError, 'Withdrawn expansion E06'):
                benchmark_run._reject_withdrawn_e06(path, ['E06'])
            old_case = next(item for item in old['cases'] if item['id'] == 'E06')
            old_case['title'] += ' modified'
            path.write_text(json.dumps(old, ensure_ascii=False), encoding='utf8')
            with self.assertRaisesRegex(ValueError, 'Withdrawn expansion E06'):
                benchmark_run._reject_withdrawn_e06(path, ['E06'])
            old_case['id'] = 'E06_RENAMED'
            path.write_text(json.dumps(old, ensure_ascii=False), encoding='utf8')
            with self.assertRaisesRegex(ValueError, 'Withdrawn expansion E06'):
                benchmark_run._reject_withdrawn_e06(path, ['E06_RENAMED'])
            benchmark_run._reject_withdrawn_e06(path, ['E07'])

    def test_run_blocks_withdrawn_but_report_reads_historical_batch(self):
        old = Path(__file__).resolve().parents[1] / 'docs/agent-eval/expansion-50-v1/cases.json'
        manifest = {'dataset_path': str(old), 'selected_case_ids': ['E06']}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = root / 'data/agent-benchmark-v2/historical'
            batch.mkdir(parents=True)
            (batch / 'manifest.json').write_text(json.dumps(manifest), encoding='utf8')
            with patch.object(benchmark_run, 'REPO', root), \
                 patch('creatoros.evaluation.benchmark.manifest.verify_manifest', return_value={'manifest': manifest}), \
                 patch('creatoros.evaluation.benchmark.manifest.frozen_dataset_path', return_value=old), \
                 patch('creatoros.evaluation.benchmark.report.summarize_batch') as summarize:
                with patch('sys.argv', ['run', 'run', '--batch', 'historical']):
                    with self.assertRaisesRegex(ValueError, 'Withdrawn expansion E06'):
                        benchmark_run.main()
                summarize.assert_not_called()
                with patch('sys.argv', ['run', 'report', '--batch', 'historical']):
                    benchmark_run.main()
                summarize.assert_called_once()


if __name__ == '__main__':
    unittest.main()
