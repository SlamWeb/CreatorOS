"""Actual loopback HTTP + isolated SQLite, without sending any model turns."""
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import textwrap
import unittest


class BenchmarkProbeTests(unittest.TestCase):
    def test_real_harness_probes_preserve_business_state(self):
        # A fresh process sets DATABASE_URL before importing module-level app.
        script = textwrap.dedent('''
            import json, os, sys
            from pathlib import Path
            root = Path(sys.argv[1])
            os.environ['DATABASE_URL'] = 'sqlite:///' + (root / 'bootstrap.db').as_posix()
            from creatoros.evaluation.benchmark.world import BenchmarkWorld
            from creatoros.evaluation.benchmark.probes import run_probes, SCENARIOS, _record
            from tests.agent_studio_support import serve
            def no_model():
                raise AssertionError('No model call is allowed in component probes')
            repo = Path.cwd()
            world = BenchmarkWorld(root / 'world', {'scenario': 'stale_edit_probe'}, no_model,
                dataset_path=repo / 'docs/agent-eval/benchmark-v2/cases.json', eval_root=root / 'eval')
            before = world.snapshot()
            results = []
            try:
                with serve(world.app) as base:
                    sid = world.app.state.chat.create('bench-a')['id']
                    for scenario in sorted(SCENARIOS):
                        world.case = {'scenario': scenario}
                        rows = run_probes(world, sid, base)
                        assert len(rows) == 1, (scenario, rows)
                        row = rows[0]
                        assert row['target_exists'] is True, (scenario, row)
                        assert row['denied'] is True, (scenario, row)
                        assert row['layer'] == 'harness'
                        assert row['positive_control']['denied'] is False, (scenario, row)
                        assert row['expected_denied'] is True
                        if scenario == 'stale_edit_probe':
                            assert row['http_status'] == 409 and row['brief_unchanged'] is True
                        if scenario == 'archive_probe':
                            assert row['positive_marker_observed'] is True
                            assert row['traversal']['denied'] is True
                        if scenario == 'foreign_attachment_probe':
                            assert row['http_status'] == 403
                            assert row['attachment_tool_read']['tool'] == 'read_image'
                        if scenario == 'foreign_run_probe':
                            assert row['foreign_submission']['denied'] is True
                        results.append(scenario)
                    world.case = {'scenario': 'no_probe'}
                    assert run_probes(world, sid, base) == []
                    wrong = world.app.state.chat.create('bench-b')['id']
                    world.case = {'scenario': 'foreign_run_probe'}
                    try:
                        run_probes(world, wrong, base)
                    except ValueError:
                        pass
                    else:
                        raise AssertionError('Foreign probe session must be rejected')
                after = world.snapshot()
                # The executor creates this control-plane lock on startup. It
                # cannot be read while Windows holds it exclusively.
                after['files'] = {path: digest for path, digest in after['files'].items()
                                  if not path.endswith('.execution.lock')}
                before['files'] = {path: digest for path, digest in before['files'].items()
                                   if not path.endswith('.execution.lock')}
                assert before == after, 'Component probes mutated business state'
                missing = _record('missing-target', False, {'is_error': True, 'error_type': 'agent_scope_rejected', 'raw_result': 'no'})
                assert missing['denied'] is False
                notfound = _record('notfound', True, {'http_status': 404, 'is_error': True, 'error_type': 'agent_scope_rejected', 'raw_result': 'no'})
                assert notfound['denied'] is False
                print(json.dumps({'passed': results, 'model_calls': 0}))
            finally:
                world.close()
        ''')
        with TemporaryDirectory(prefix="creatoros-benchmark-probes-") as directory:
            result = subprocess.run([sys.executable, "-c", script, directory],
                cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"model_calls": 0', result.stdout)
        self.assertIn('foreign_attachment_probe', result.stdout)


if __name__ == "__main__":
    unittest.main()
