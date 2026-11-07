"""Formal E06 revision: frozen identity, separate namespace and no historical re-execution."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from creatoros.evaluation import regression_runner, tool_calling
from creatoros.evaluation.benchmark.nonvideo_guard import evidence, policy, validate
from creatoros.evaluation.benchmark.run import freeze
from creatoros.evaluation.benchmark.manifest import verify_manifest
from creatoros.evaluation.comparison import CONTRACT_FIELDS, compare_runs


class E06FormalRegressionTests(unittest.TestCase):
    def setUp(self):
        self.prepared = regression_runner.prepare('regular-v7', ['e06-scope:E06'], ['judge'])
        self.binding = self.prepared['catalog_binding']

    def test_selection_and_old_sources_cannot_bypass_withdrawal(self):
        self.assertFalse(self.prepared['blocked'])
        self.assertEqual(self.prepared['requires_local_dispatch'], 'local-dispatch-v6')
        regression_runner.validate_frozen_binding(['E06'], self.binding)
        for suite in ('regular-v5', 'regular-v6', 'full-v5', 'full-v6'):
            with self.subTest(suite=suite):
                self.assertTrue(regression_runner.prepare(suite, ['expansion:E06'])['blocked'])
        for selected in (['e06-scope:E06', 'natural:B06'], ['e06-scope:E06', 'expansion:E07'],
                         ['e06-scope:E06', 'e06-scope:E06']):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                regression_runner.prepare('regular-v7', selected, ['judge'])
        for source in ('base', 'natural', 'expansion'):
            with self.assertRaises(ValueError):
                policy(['E06'], local_dispatch='local-dispatch-v6', source_namespace=source)
        for version in ('v1', 'v2', 'v3', 'v4', 'v5', 'v6'):
            self.assertEqual(regression_runner.prepare('regular-' + version, ['base:B06'])['blocked'], [])
        # v7 contains both adapters: the old case must still execute using v5.
        old = regression_runner.prepare('regular-v7', ['base:B06'], ['judge'])
        self.assertFalse(old['blocked'])
        data, meta = tool_calling.suite_and_metadata(['B06'], local_dispatch='local-dispatch-v5', catalog_binding=old['catalog_binding'])
        self.assertEqual(meta['source_namespace'], 'base')
        self.assertEqual(data['dataset_id'], tool_calling.REGRESSION_SUITE_ID)

    def test_real_freeze_and_cross_source_rejection(self):
        path, expected = tool_calling._execution_dataset('local-dispatch-v6', self.binding)
        self.assertEqual(expected, tool_calling.E06_SCOPE_SUITE_ID)
        dataset, meta = tool_calling.suite_and_metadata(['E06'], local_dispatch='local-dispatch-v6', catalog_binding=self.binding)
        self.assertEqual(meta['source_namespace'], 'e06-scope')
        self.assertEqual(dataset['dataset_id'], expected)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = freeze(root, path, ['E06'], nonvideo_guard=True, local_dispatch='local-dispatch-v6', catalog_binding=self.binding)
            self.assertEqual(verify_manifest(root / 'manifest.json')['manifest']['dataset_id'], expected)
            self.assertTrue(validate(manifest))
            altered = copy.deepcopy(manifest)
            altered['model_configuration']['benchmark_guard']['source_namespace'] = 'expansion'
            with self.assertRaises(ValueError): validate(altered)
            changed = copy.deepcopy(self.binding)
            next(row for row in changed['frozen_catalog']['source']['sources'] if row['namespace'] == 'e06-scope')['canonical_sha256'] = 'f' * 64
            with self.assertRaises(ValueError): tool_calling._execution_dataset('local-dispatch-v6', changed)

    def test_new_dataset_registration_precedes_controlled_run(self):
        from creatoros.evaluation.registry import EvalRegistry
        path, identity = tool_calling._execution_dataset('local-dispatch-v6', self.binding)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); batch = root / 'data/agent-benchmark-v2/e06-run'; registry = EvalRegistry(root / 'registry.sqlite')
            commands = []
            def invoke(args):
                commands.append(args[3])
                if args[3] == 'freeze':
                    self.assertEqual(registry.get_run('e06-run')['status'], 'freezing')
                    binding = json.loads(Path(args[args.index('--catalog-binding-file') + 1]).read_text(encoding='utf8'))
                    batch.mkdir(parents=True)
                    freeze(batch, path, ['E06'], nonvideo_guard=True, local_dispatch='local-dispatch-v6', catalog_binding=binding)
                    return 0
                self.assertEqual(registry.get_run('e06-run')['status'], 'running')
                return 9
            with patch.object(tool_calling, 'ROOT', root), patch.object(tool_calling, '_execution_dataset', return_value=(path, identity)), \
                 patch.object(tool_calling, '_hash_files', return_value='a' * 64), patch.object(tool_calling, '_driver_files', return_value=[]):
                result = tool_calling.run_registered(registry, round_data={'id':'round', 'title':'e06', 'code_version':'test', 'changes':['judge']},
                    run_id='e06-run', cases=['E06'], allow_paid=True, invoke=invoke, local_dispatch='local-dispatch-v6', catalog_binding=self.binding)
            run = registry.get_run('e06-run')
            self.assertEqual(commands, ['freeze','run'])
            self.assertEqual(result['status'], 'failed')
            self.assertEqual((run['suite_id'], run['suite_version']), (tool_calling.E06_SCOPE_SUITE_ID, 'v2'))
            self.assertEqual(run['metadata']['source_namespace'], 'e06-scope')
            self.assertEqual(run['manifest_sha256'], tool_calling._sha((batch/'manifest.json').read_bytes()))

    def test_sync_recovery_and_cross_batch_evidence(self):
        case, run_id, record_id = 'E06', 'e06-run', 'b' * 32
        path, dataset_id = tool_calling._execution_dataset('local-dispatch-v6', self.binding)
        dataset, metadata = tool_calling.suite_and_metadata([case], local_dispatch='local-dispatch-v6', catalog_binding=self.binding)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); batch = root / 'data/agent-benchmark-v2' / run_id
            record = batch / 'records' / record_id; record.mkdir(parents=True)
            manifest = freeze(batch, path, [case], nonvideo_guard=True, local_dispatch='local-dispatch-v6', catalog_binding=self.binding)
            verify_manifest(batch / 'manifest.json')
            digest = tool_calling._sha((batch / 'manifest.json').read_bytes())
            guard = manifest['model_configuration']['benchmark_guard']
            (record / 'guard.json').write_text(json.dumps({'policy':guard,'case_id':case,'video_dispatch_installed':True,
                'publication_client_installed':True,'network_sandbox':False,'local_dispatch_installed':True,'background_services_deferred':True}))
            shared = {'benchmark_batch_id':run_id,'benchmark_manifest_sha256':digest}
            claim = {'case_id':case,'run_id':record_id,'first_attempt':True,**shared}
            (batch / 'claims').mkdir(); (batch / f'claims/{case}.json').write_text(json.dumps(claim))
            report = {'case_id':case,'run_id':record_id,'dataset_id':dataset_id,**shared,'auto_status':'passed',
                'semantic_status':'passed','execution_status':'completed','dimensions':{'boundary_enforced':'passed'},'error':None}
            (record / 'report.json').write_text(json.dumps(report))
            row = {key:report[key] for key in ('case_id','auto_status','semantic_status','execution_status','dimensions')}
            row.update(status='passed',observed_error=None,record_paths=[f'records/{record_id}/report.json'],benchmark_guard=evidence(manifest,case,record))
            (batch / 'summary.json').write_text(json.dumps({'dataset_id':dataset_id,'planned_case_ids':[case],'cases':[row]}))
            definition = next(item for item in dataset['cases'] if item['id'] == case)
            class Registry:
                def __init__(self): self.added, self.recovered = [], []
                def get_run(self, _): return {'id':run_id,'round_id':'round','status':'interrupted','suite_id':dataset_id,
                    'suite_version':'v2','manifest_sha256':digest,'planned_cases':[case],'metadata':metadata,'results':[],'recoveries':[]}
                def round_detail(self, _): return {'round':{'code_version':'a'*40}}
                def add_case_result(self, _, result): self.added.append(result)
                def recover_case_result(self, _, result, **_kw): self.recovered.append(result)
            registry = Registry()
            self.assertEqual(tool_calling.sync_summary(registry,run_id,batch,{case:definition},dataset_id=dataset_id),1)
            self.assertEqual(registry.added[0]['case_definition_hash'],tool_calling._definition_hash(definition))
            for alteration in ({'dataset_id':tool_calling.EXPANSION_SUITE_ID},{'benchmark_batch_id':'other'}):
                (record / 'report.json').write_text(json.dumps({**report,**alteration}))
                with self.assertRaisesRegex(ValueError, 'Report does not match'):
                    tool_calling.sync_summary(registry,run_id,batch,{case:definition},dataset_id=dataset_id)
            (record / 'report.json').write_text(json.dumps(report))
            (batch / f'claims/{case}.json').write_text(json.dumps({**claim,'benchmark_batch_id':'other'}))
            with self.assertRaisesRegex(ValueError, 'claim'):
                tool_calling.sync_summary(registry,run_id,batch,{case:definition},dataset_id=dataset_id)
            (batch / f'claims/{case}.json').write_text(json.dumps(claim))
            fake = copy.deepcopy(manifest); fake['git'] = {'sha':'a'*40,'dirty':False,'status_available':True}
            with patch.object(tool_calling,'ROOT',root), patch.object(tool_calling,'suite_and_metadata',return_value=(dataset,metadata)), \
                 patch('creatoros.evaluation.benchmark.manifest.verify_manifest',return_value={'manifest':fake}):
                tool_calling.recover_registered(registry,run_id)
            self.assertEqual(len(registry.recovered),1)

    def test_cross_namespace_cannot_compare_even_if_hashes_equal(self):
        meta = {key:'same' for key in CONTRACT_FIELDS}; meta['execution_mode']='local-dispatch-v6'
        result={'case_id':'E06','status':'passed','case_definition_hash':'same','round_attempt':1}
        a={'id':'a','metadata':{**meta,'source_namespace':'e06-scope'},'planned_cases':['E06'],'results':[result]}
        b={'id':'b','metadata':{**meta,'source_namespace':'expansion'},'planned_cases':['E06'],'results':[result]}
        self.assertFalse(compare_runs(a,b)['contract_compatible'])


if __name__ == '__main__': unittest.main()
