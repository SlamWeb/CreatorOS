"""Identity expansion: task fixtures and positive, negative, missing evidence checks."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT/'docs/agent-eval/expansion-identity/cases.json'


class IdentityExpansionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = json.loads(DATASET.read_text(encoding='utf-8'))
        cls.cases = {case['id']:case for case in cls.doc['cases']}

    def test_suite_registered_cases_and_precise_quoted_text(self):
        from creatoros.evaluation.benchmark.manifest import _dataset
        loaded = _dataset(DATASET)
        self.assertEqual(len(loaded['cases']),8)
        for case in loaded['cases']:
            with self.subTest(id=case['id']):
                self.assertEqual(case['scenario'],'identity_expansion')
                self.assertEqual(case['origin']['type'],'synthetic')
                self.assertFalse(case['trajectory']['required'])
                for rule in case['expectations']:
                    if rule.get('kind') == 'topic_field':
                        self.assertIn('「'+rule['value']+'」',case['steps'][-1]['text'])

    def test_update_cases_positive_wrong_target_other_field_and_missing(self):
        from creatoros.evaluation.benchmark.grader import _expectation, MissingEvidence
        targets={'E02':'identity-weather-short','E03':'identity-office','E04':'bench-topic-a','E08':'identity-queue-second'}
        for ident,target in targets.items():
            case=self.cases[ident]
            before=[{'id':target,'title':'旧标题','brief':'旧切入点','status':'queued'},
                    {'id':'other','title':'同名或相似选题','brief':'另一条','status':'queued'}]
            after=deepcopy(before)
            for exp in case['expectations']:
                if exp['kind']=='topic_field': after[0][exp['field']]=exp['value']
            evidence={'before':{'database':{'topics':before,'content_runs':[]}},
                      'after':{'database':{'topics':after,'content_runs':[]}},'variables':{'topic_a':'bench-topic-a'}}
            with self.subTest(case=ident):
                self.assertTrue(all(_expectation(exp,evidence)[0] for exp in case['expectations']))
                wrong=deepcopy(evidence);wrong['after']['database']['topics'][0]['brief']='旧切入点'
                self.assertFalse(_expectation(case['expectations'][1],wrong)[0])
                if ident == 'E04':
                    wrong_title=deepcopy(evidence);wrong_title['after']['database']['topics'][0]['title']='旧标题'
                    self.assertFalse(_expectation(case['expectations'][2],wrong_title)[0])
                extra=deepcopy(evidence);extra['after']['database']['topics'][1]['brief']='擅自修改'
                other_rule=next(x for x in case['expectations'] if x.get('exclude_ids'))
                self.assertFalse(_expectation(other_rule,extra)[0])
                missing=deepcopy(evidence);del missing['after']['database']['topics']
                with self.assertRaises(MissingEvidence):_expectation(case['expectations'][1],missing)

    def test_each_semantic_gate_preserves_pass_fail_and_unknown(self):
        # Controlled Judge labels check scoring wiring, not Judge accuracy.
        from creatoros.evaluation.benchmark.scoring import score_case
        for case in self.cases.values():
            ids=[x['id'] for x in case['scoring']['program_checks']] + case['scoring']['critical_checks']
            grade={'checks':[{'id':i,'status':'passed'} for i in ids]}
            for value,expected in ((True,'passed'),(False,'failed'),(None,'needs_review')):
                judge={'items':[{'id':case['rubric'][0]['id'],'pass':value,'evidence':'controlled label'}]}
                with self.subTest(id=case['id'],label=value):
                    score=score_case(case,grade,judge)
                    self.assertEqual(score['semantic']['status'],expected)
                    self.assertEqual(score['critical_status'],expected)

    def test_e01_archived_twin_preserved_and_only_active_removed(self):
        from creatoros.evaluation.benchmark.grader import _expectation,MissingEvidence
        old=[{'id':'identity-archived','title':'周末出游'}, {'id':'identity-active','title':'周末出游'}]
        removed=[{'topic_id':'identity-archived','status':'archived'}]
        e={'before':{'database':{'topics':old,'topic_removals':removed,'content_runs':[]}},
           'after':{'database':{'topics':old[:1], 'topic_removals':removed+[{'topic_id':'identity-active','status':'deleted'}], 'content_runs':[]}},'variables':{}}
        case=self.cases['E01']
        self.assertTrue(all(_expectation(rule,e)[0] for rule in case['expectations']))
        bad=deepcopy(e);bad['after']['database']['topics']=[]
        self.assertFalse(_expectation(case['expectations'][1],bad)[0])
        unknown=deepcopy(e);unknown['before']['database']['topics']=[]
        with self.assertRaises(MissingEvidence):_expectation(case['expectations'][1],unknown)

    def test_clarification_and_missing_status_read_only(self):
        from creatoros.evaluation.benchmark.grader import _expectation, MissingEvidence
        snapshot={'database':{'topics':[{'id':'t','title':'旧标题'}]},'files':{'asset':'unchanged'}}
        for ident in ('E05','E06','E07'):
            case=self.cases[ident]; exp=case['expectations'][0]
            with self.subTest(id=ident):
                data={'before':deepcopy(snapshot),'after':deepcopy(snapshot),'variables':{}}
                self.assertTrue(_expectation(exp,data)[0])
                data['after']['database']['topics'][0]['title']='意外修改'
                self.assertFalse(_expectation(exp,data)[0])
                del data['before']['files']
                with self.assertRaises(MissingEvidence):_expectation(exp,data)
        self.assertIn('files',snapshot)

    def test_isolated_real_world_fixture(self):
        code = r"""
import json, os, sys
from pathlib import Path
root=Path(sys.argv[1]); path=Path(sys.argv[2])
os.environ['DATABASE_URL']='sqlite:///'+(root/'world'/'world.db').as_posix()
from creatoros.evaluation.benchmark.world import BenchmarkWorld
from creatoros.evaluation.benchmark.expansion_identity import seed
cases=json.loads(path.read_text(encoding='utf-8'))['cases']
w=BenchmarkWorld(root/'world',cases[0],None,dataset_path=path,eval_root=root/'eval')
try:
 for case in cases[1:]: seed(w,case)
 snapshot=w.snapshot(); rows=snapshot['database']['topics']; removed=snapshot['database']['topic_removals']
 assert {r['id'] for r in rows if r['title']=='周末出游'} == {'identity-active','identity-archived'}
 assert any(r['topic_id']=='identity-archived' and r['status']=='archived' for r in removed)
 assert {r['title'] for r in rows if r['id'] in ('identity-weather-short','identity-weather-long')} == {'天气变化','天气变化进阶'}
 assert {r['id'] for r in rows if r['title']=='借用表达'} == {'identity-office','identity-library'}
 q=sorted((r for r in rows if r['series_id']=='identity-short-queue'),key=lambda r:r['position'])
 assert [r['id'] for r in q] == ['identity-queue-first','identity-queue-second']
finally: w.close()
"""
        with TemporaryDirectory() as temp:
            result=subprocess.run([sys.executable,'-c',code,temp,str(DATASET)],cwd=ROOT,
                                  capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120)
            self.assertEqual(result.returncode,0,result.stderr[-4000:])

if __name__=='__main__':unittest.main()
