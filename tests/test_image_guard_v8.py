import unittest
import tempfile
import json
import copy
import os
import subprocess
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from creatoros.ai.types import ToolCall
from creatoros.evaluation.benchmark.local_dispatch_guard import DispatchGuard
from creatoros.evaluation.benchmark.nonvideo_guard import policy
from creatoros.tools.results import ToolResult


class ImageGuardV8Tests(unittest.TestCase):
    def test_binding_rejects_linked_ancestor_and_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp).resolve()
            trusted = parent / 'repo'
            trusted.mkdir()
            outside = parent / 'outside'
            (outside / 'trial' / 'world').mkdir(parents=True)
            linked = trusted / 'trial-link'
            os.symlink(outside, linked, target_is_directory=True)
            world = SimpleNamespace(root=(outside / 'trial' / 'world').resolve())
            with self.assertRaises(RuntimeError):
                DispatchGuard('local-dispatch-v8').bind_world(world, linked / 'trial' / 'world', trusted_root=trusted)
            with self.assertRaises(RuntimeError):
                DispatchGuard('local-dispatch-v8').bind_world(world, trusted / '..' / 'outside' / 'trial' / 'world', trusted_root=trusted)
            with self.assertRaises(RuntimeError):
                DispatchGuard('local-dispatch-v8').bind_world(world, outside / 'trial' / 'world', trusted_root=trusted)
            self.assertFalse(DispatchGuard._safe_under(trusted, linked / 'trial' / 'world'))
            if os.name == 'nt':
                junction = trusted / 'junction'
                created = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(outside)],
                                         capture_output=True, text=True)
                self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
                try:
                    self.assertFalse(DispatchGuard._safe_under(trusted, junction / 'trial' / 'world'))
                    with self.assertRaises(RuntimeError):
                        DispatchGuard('local-dispatch-v8').bind_world(world, junction / 'trial' / 'world', trusted_root=trusted)
                finally:
                    os.rmdir(junction)

    def test_unbound_world_cannot_dispatch_image_or_run_reads(self):
        for version in ('local-dispatch-v1', 'local-dispatch-v2', 'local-dispatch-v3',
                        'local-dispatch-v4', 'local-dispatch-v5', 'local-dispatch-v6',
                        'local-dispatch-v7', 'local-dispatch-v8'):
            for tool in ('read_image', 'get_content_run'):
                with self.subTest(version=version, tool=tool):
                    guard = DispatchGuard(version)
                    result = guard.wrap(lambda *_args, **_kw: ToolResult('forwarded'))(
                        ToolCall('call', tool, '{}'), model_requested=True)
                    self.assertTrue(result.is_error)
                    self.assertEqual(result.error_type, 'local_dispatch_guard_denied')

    def test_real_frozen_selection_registration_and_binding(self):
        from creatoros.evaluation import regression_runner, tool_calling
        from creatoros.evaluation.registry import EvalRegistry
        from creatoros.evaluation.benchmark.manifest import verify_manifest
        from creatoros.evaluation.benchmark.run import freeze
        prepared = regression_runner.prepare('regular-v9', ['base:B29'], ['agent_shared'])
        self.assertFalse(prepared['blocked'])
        binding = prepared['catalog_binding']
        self.assertEqual(prepared['requires_local_dispatch'], 'local-dispatch-v8')
        path, dataset_id = tool_calling._execution_dataset('local-dispatch-v8', binding)
        dataset, metadata = tool_calling.suite_and_metadata(['B29'], local_dispatch='local-dispatch-v8', catalog_binding=binding)
        self.assertEqual(metadata['source_namespace'], 'base')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            batch = root / 'data/agent-benchmark-v2/material-run'
            registry = EvalRegistry(root / 'registry.sqlite')
            def invoke(args):
                if args[3] == 'freeze':
                    batch.mkdir(parents=True)
                    bound = json.loads(Path(args[args.index('--catalog-binding-file') + 1]).read_text(encoding='utf-8'))
                    freeze(batch, path, ['B29'], nonvideo_guard=True, local_dispatch='local-dispatch-v8', catalog_binding=bound)
                    return 0
                return 9  # No paid model and no browser.
            with patch.object(tool_calling, 'ROOT', root), \
                 patch.object(tool_calling, '_execution_dataset', return_value=(path, dataset_id)), \
                 patch.object(tool_calling, '_hash_files', return_value='a' * 64), \
                 patch.object(tool_calling, '_driver_files', return_value=[]):
                result = tool_calling.run_registered(registry, round_data={'id': 'round', 'title': 'material',
                    'code_version': 'test', 'changes': ['agent_shared']}, run_id='material-run', cases=['B29'],
                    allow_paid=True, invoke=invoke, local_dispatch='local-dispatch-v8', catalog_binding=binding)
            self.assertEqual(result['status'], 'failed')
            run = registry.get_run('material-run')
            self.assertEqual(run['suite_id'], dataset_id)
            self.assertEqual(run['metadata']['source_namespace'], 'base')
            verified = verify_manifest(batch / 'manifest.json')['manifest']
            self.assertEqual(verified['model_configuration']['benchmark_guard']['local_dispatch'], 'local-dispatch-v8')
            regression_runner.validate_frozen_binding(['B29'], binding)
            self.assertEqual(regression_runner.prepare('regular-v7', ['e06-scope:E06'])['requires_local_dispatch'], 'local-dispatch-v6')
            with self.assertRaises(ValueError):
                regression_runner.prepare('regular-v9', ['base:B29', 'expansion:P01'], ['agent_shared'])

    def test_v8_sync_recovery_and_wrong_source_rejected(self):
        from creatoros.evaluation import regression_runner, tool_calling
        from creatoros.evaluation.benchmark.nonvideo_guard import evidence
        from creatoros.evaluation.benchmark.run import freeze
        case, run_id, record_id = 'B29', 'run01', 'b' * 32
        binding = regression_runner.prepare('regular-v9', ['base:B29'], ['agent_shared'])['catalog_binding']
        path, dataset_id = tool_calling._execution_dataset('local-dispatch-v8', binding)
        dataset, metadata = tool_calling.suite_and_metadata([case], local_dispatch='local-dispatch-v8', catalog_binding=binding)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            batch = root / 'data/agent-benchmark-v2' / run_id
            record = batch / 'records' / record_id
            record.mkdir(parents=True)
            manifest = freeze(batch, path, [case], nonvideo_guard=True, local_dispatch='local-dispatch-v8', catalog_binding=binding)
            digest = tool_calling._sha((batch / 'manifest.json').read_bytes())
            guard = manifest['model_configuration']['benchmark_guard']
            (record / 'guard.json').write_text(json.dumps({'policy': guard, 'case_id': case,
                'video_dispatch_installed': True, 'publication_client_installed': True, 'network_sandbox': False,
                'local_dispatch_installed': True, 'background_services_deferred': True}))
            shared = {'benchmark_batch_id': run_id, 'benchmark_manifest_sha256': digest}
            claim = {'case_id': case, 'run_id': record_id, 'first_attempt': True, **shared}
            (batch / 'claims').mkdir()
            (batch / f'claims/{case}.json').write_text(json.dumps(claim))
            report = {'case_id': case, 'run_id': record_id, 'dataset_id': dataset_id, **shared,
                      'auto_status': 'passed', 'semantic_status': 'passed', 'execution_status': 'completed',
                      'dimensions': {'boundary_enforced': 'passed'}, 'error': None}
            (record / 'report.json').write_text(json.dumps(report))
            row = {key: report[key] for key in ('case_id', 'auto_status', 'semantic_status', 'execution_status', 'dimensions')}
            row.update(status='passed', observed_error=None, record_paths=[f'records/{record_id}/report.json'],
                       benchmark_guard=evidence(manifest, case, record))
            (batch / 'summary.json').write_text(json.dumps({'dataset_id': dataset_id, 'planned_case_ids': [case], 'cases': [row]}))
            definition = next(item for item in dataset['cases'] if item['id'] == case)
            class Registry:
                def __init__(self): self.added, self.recovered = [], []
                def get_run(self, _): return {'id': run_id, 'round_id': 'round', 'status': 'interrupted',
                    'suite_id': dataset_id, 'suite_version': 'v3.4', 'manifest_sha256': digest,
                    'planned_cases': [case], 'metadata': metadata, 'results': [], 'recoveries': []}
                def round_detail(self, _): return {'round': {'code_version': 'a' * 40}}
                def add_case_result(self, _, result): self.added.append(result)
                def recover_case_result(self, _, result, **_kw): self.recovered.append(result)
            registry = Registry()
            self.assertEqual(tool_calling.sync_summary(registry, run_id, batch, {case: definition}, dataset_id=dataset_id), 1)
            (record / 'report.json').write_text(json.dumps({**report, 'dataset_id': tool_calling.EXPANSION_SUITE_ID}))
            with self.assertRaises(ValueError):
                tool_calling.sync_summary(registry, run_id, batch, {case: definition}, dataset_id=dataset_id)
            (record / 'report.json').write_text(json.dumps(report))
            fake = copy.deepcopy(manifest)
            fake['git'] = {'sha': 'a' * 40, 'dirty': False, 'status_available': True}
            with patch.object(tool_calling, 'ROOT', root), \
                 patch.object(tool_calling, 'suite_and_metadata', return_value=(dataset, metadata)), \
                 patch('creatoros.evaluation.benchmark.manifest.verify_manifest', return_value={'manifest': fake}):
                tool_calling.recover_registered(registry, run_id)
            self.assertEqual(len(registry.recovered), 1)


if __name__ == '__main__':
    unittest.main()
