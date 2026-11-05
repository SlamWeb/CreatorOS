from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'docs/agent-eval/expansion-extra/cases.json'


class ExtraTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from creatoros.evaluation.benchmark.manifest import _dataset
        cls.cases = _dataset(DATA)['cases']

    def test_each_program_rule_positive_negative_missing(self):
        from creatoros.evaluation.benchmark.grader import _expectation, MissingEvidence
        for case in self.cases:
            ident = case['id']
            old = {
                'X01': [('extra-append','问路用语','场景：车站问路。')],
                'X02': [('extra-swap-a','迟到表达','办公室场景'), ('extra-swap-b','道歉表达','课堂场景')],
                'X03': [('extra-filter-a','短语甲','需校对：第一个'), ('extra-filter-b','短语乙','已经校对'), ('extra-filter-c','短语丙','需校对：第二个')],
                'X04': [],
            }[ident]
            topics = [{'id':i,'title':t,'brief':b,'series_id':'bench-english'} for i,t,b in old]
            topics.append({'id':'unrelated','title':'其他','brief':'保留','series_id':'bench-english'})
            before = {'topics':topics,'content_runs':[]}
            after = deepcopy(before)
            for rule in case['expectations']:
                if rule['kind'] == 'topic_field':
                    next(x for x in after['topics'] if x['id'] == rule['id'])[rule['field']] = rule['value']
                if rule['kind'] == 'new_rows_match':
                    after['topics'].extend([{'id':'new-1',**rule['value'][0]}, {'id':'new-2',**rule['value'][1]}])
            evidence = {'before':{'database':before},'after':{'database':after},'variables':{}}
            with self.subTest(id=ident):
                self.assertTrue(all(_expectation(rule,evidence)[0] for rule in case['expectations']))
                wrong=deepcopy(evidence)
                if ident == 'X04': wrong['after']['database']['topics'][-1]['brief']='办公室借笔'
                else:
                    field_rule=next(x for x in case['expectations'] if x['kind']=='topic_field')
                    next(x for x in wrong['after']['database']['topics'] if x['id']==field_rule['id'])[field_rule['field']]='错误'
                self.assertFalse(all(_expectation(rule,wrong)[0] for rule in case['expectations']))
                extra=deepcopy(evidence); extra['after']['database']['topics'][len(topics)-1]['brief']='擅自修改'
                self.assertFalse(all(_expectation(rule,extra)[0] for rule in case['expectations']))
                missing=deepcopy(evidence); del missing['after']['database']['topics']
                with self.assertRaises(MissingEvidence): _expectation(case['expectations'][0],missing)

    def test_semantic_labels_include_unknown(self):
        from creatoros.evaluation.benchmark.scoring import score_case
        for case in self.cases:
            checks=[x['id'] for x in case['scoring']['program_checks']]+case['scoring']['critical_checks']
            grade={'checks':[{'id':x,'status':'passed'} for x in checks]}
            for label, status in [(True,'passed'),(False,'failed'),(None,'needs_review')]:
                score=score_case(case,grade,{'items':[{'id':case['rubric'][0]['id'],'pass':label,'evidence':'controlled label'}]})
                self.assertEqual(score['semantic']['status'],status)

    def test_seed_in_disposable_real_world(self):
        script = r'''
import json, os, sys
from pathlib import Path
root=Path(sys.argv[1]); data=Path(sys.argv[2]); cases=json.loads(data.read_text(encoding='utf8'))['cases']
os.environ['DATABASE_URL']='sqlite:///'+(root/'world'/'world.db').as_posix()
from creatoros.evaluation.benchmark.world import BenchmarkWorld
from creatoros.evaluation.benchmark.expansion_extra import seed
w=BenchmarkWorld(root/'world',cases[0],None,dataset_path=data,eval_root=root/'eval')
try:
 seeded={row["id"] for row in w.snapshot()["database"]["topics"]}
 for c in cases:
  if c["id"] != "X01" or "extra-append" not in seeded: seed(w,c)
 rows=w.snapshot()['database']['topics']
 actual={r['id']:(r['title'],r['brief']) for r in rows if r['id'].startswith('extra-')}
 assert len(actual)==6, actual
 assert actual['extra-append']==('问路用语','场景：车站问路。')
 assert actual['extra-swap-a'][1]=='办公室场景'
 assert actual['extra-filter-b'][1]=='已经校对'
finally: w.close()
'''
        with TemporaryDirectory() as temp:
            result=subprocess.run([sys.executable,'-c',script,temp,str(DATA)],cwd=ROOT,capture_output=True,text=True,encoding='utf8',errors='replace',timeout=120)
            self.assertEqual(result.returncode,0,result.stderr[-5000:])

if __name__ == '__main__': unittest.main()
