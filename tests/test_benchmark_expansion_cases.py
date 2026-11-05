"""New tasks: independent fixtures and exact-target oracle regression."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / 'docs/agent-eval/expansion-v2/cases.json'


class ExpansionCaseTests(unittest.TestCase):
    def setUp(self):
        self.cases = {c['id']: c for c in json.loads(DATASET.read_text(encoding='utf-8'))['cases']}

    def test_exact_target_and_unicode_preservation_oracles(self):
        from creatoros.evaluation.benchmark.grader import _expectation
        old = [{'id':'bench-topic-01','title':'同名选题','brief':'旧1','status':'queued'},
               {'id':'bench-topic-02','title':'同名选题','brief':'旧2','status':'queued'}]
        good = copy.deepcopy(old); good[0]['brief']='用图书馆借书场景'
        data={'before':{'database':{'topics':old,'content_runs':[]}},
              'after':{'database':{'topics':good,'content_runs':[]}},'variables':{}}
        case=self.cases['D01']
        self.assertTrue(all(_expectation(x,data)[0] for x in case['expectations']))
        wrong=copy.deepcopy(data);wrong['after']['database']['topics'][1]['brief']='用图书馆借书场景'
        self.assertFalse(_expectation(case['expectations'][3],wrong)[0])
        extra=copy.deepcopy(data);extra['after']['database']['topics'][0]['brief']+='。'
        self.assertFalse(_expectation(case['expectations'][1],extra)[0])
        data['after']['database']['topics'][0]['brief']='旧1'
        self.assertFalse(_expectation(case['expectations'][1],data)[0])
        original='第一行\n第二行：“内容” 📚'
        before=[{'id':'bench-topic-a','title':'旧标题','brief':original,'status':'queued'}]
        after=copy.deepcopy(before);after[0]['title']='借书用词：borrow 与 lend'
        data={'before':{'database':{'topics':before,'content_runs':[]}},
              'after':{'database':{'topics':after,'content_runs':[]}},'variables':{'topic_a':'bench-topic-a'}}
        case=self.cases['D04']
        self.assertTrue(all(_expectation(x,data)[0] for x in case['expectations']))
        after[0]['brief']=original.replace('📚','')
        self.assertFalse(_expectation(case['expectations'][2],data)[0])

    def test_corrected_target_and_cross_series_removal(self):
        from creatoros.evaluation.benchmark.grader import _expectation
        old=[{'id':'bench-topic-01','title':'同名选题','brief':'旧1','status':'queued'},
             {'id':'bench-topic-02','title':'同名选题','brief':'旧2','status':'queued'}]
        after=copy.deepcopy(old);after[1]['brief']='用图书馆借书场景'
        data={'before':{'database':{'topics':old,'content_runs':[]}},
              'after':{'database':{'topics':after,'content_runs':[]}},'variables':{}}
        case=self.cases['C04']
        self.assertTrue(all(_expectation(x,data)[0] for x in case['expectations']))
        after[0]['brief']='不应更改'
        self.assertFalse(_expectation(case['expectations'][3],data)[0])
        old=[{'id':'bench-topic-01','series_id':'bench-english','title':'同名选题'},
             {'id':'bench-spare-duplicate','series_id':'bench-spare','title':'同名选题'}]
        receipt={'topic_id':'bench-spare-duplicate','status':'deleted'}
        data={'before':{'database':{'topics':old,'topic_removals':[],'content_runs':[]}},
              'after':{'database':{'topics':old[:1],'topic_removals':[receipt],'content_runs':[]}},'variables':{}}
        case=self.cases['D02']
        self.assertTrue(all(_expectation(x,data)[0] for x in case['expectations']))
        data['after']['database']['topics']=old[1:]
        self.assertFalse(_expectation(case['expectations'][1],data)[0])

    def test_real_isolated_world_has_two_same_named_topics_and_unicode_fixture(self):
        code = r"""
import json, os, sys
from pathlib import Path
root=Path(sys.argv[1]); which=sys.argv[2]
os.environ['DATABASE_URL']='sqlite:///'+(root/'world'/'world.db').as_posix()
from creatoros.evaluation.benchmark.world import BenchmarkWorld
path=Path(sys.argv[3]); case=next(c for c in json.loads(path.read_text(encoding='utf-8'))['cases'] if c['id']==which)
w=BenchmarkWorld(root/'world',case,None,dataset_path=path,eval_root=root/'eval')
try:
 rows=w.before['database']['topics']
 if which in ('C02','D01','C04'):
  assert {r['id'] for r in rows if r['title']=='同名选题'} == {'bench-topic-01','bench-topic-02'}
 elif which == 'D02':
  assert any(r['id']=='bench-spare-duplicate' and r['title']=='同名选题' and r['series_id']=='bench-spare' for r in rows)
  assert any(r['id']=='bench-topic-01' and r['title']=='同名选题' and r['series_id']=='bench-english' for r in rows)
 else:
  row=next(r for r in rows if r['id']=='bench-topic-a')
  assert '\n' in row['brief'] and '📚' in row['brief']
finally:
 w.close()
"""
        def check(case_id):
            with TemporaryDirectory() as temp:
                p=subprocess.run([sys.executable,'-c',code,temp,case_id,str(DATASET)],cwd=ROOT,
                                 capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=90)
                return case_id, p.returncode, p.stderr[-3000:]
        with ThreadPoolExecutor(max_workers=3) as pool:
            for case_id, status, error in pool.map(check, self.cases):
                with self.subTest(case=case_id):
                    self.assertEqual(status,0,error)


if __name__ == '__main__': unittest.main()
