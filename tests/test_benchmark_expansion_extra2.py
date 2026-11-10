from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'docs/agent-eval/expansion-extra2/cases.json'

class Extra2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from creatoros.evaluation.benchmark.manifest import _dataset
        cls.cases={x['id']:x for x in _dataset(DATA)['cases']}

    def test_isolated_database_and_oracles(self):
        code=r'''
import json,os,sys
from pathlib import Path
root=Path(sys.argv[1]); data=Path(sys.argv[2]); ident=sys.argv[3]
os.environ['DATABASE_URL']='sqlite:///'+(root/'world'/'world.db').as_posix()
from creatoros.evaluation.benchmark.world import BenchmarkWorld
from creatoros.evaluation.benchmark.expansion_extra2 import seed
from creatoros.evaluation.benchmark.grader import _expectation,MissingEvidence
from copy import deepcopy
case=next(c for c in json.loads(data.read_text(encoding='utf8'))['cases'] if c['id']==ident)
w=BenchmarkWorld(root/'world',case,None,dataset_path=data,eval_root=root/'eval')
try:
 # Support integration before or after seed registration without inserting twice.
 if not (('extra2_item1' in w.variables) if ident=='Y02' else ('extra2_run' in w.variables) if ident=='Y03' else any(r['id']==('extra2-first-1' if ident=='Y01' else 'extra2-compare-a') for r in w.snapshot()['database']['topics'])): seed(w,case)
 before=w.snapshot()
 if ident=='Y01':
  for series in ('extra2-first','extra2-second'):
   rows=w.content.list_topics(series,include_removed=False)
   assert [r.id for r in rows]==[f'{series}-{n}' for n in range(1,4)]
  from creatoros.web.topic_mutations import edit_digest
  digest=edit_digest(w.content.get_topic('extra2-second-3'))
  w.app.state.writes.edit_topic('extra2-second-3',brief='晚间复盘场景',expected_digest=digest,request_id='extra2-edit')
 elif ident=='Y02':
  svc=w.app.state.media_collections; cid=w.variables['collection_a']; d=svc.get('bench-a',cid)
  assert [x['id'] for x in d['items'] if not x['included']]==[w.variables['extra2_item2']]
  svc.patch('bench-a',cid,expected_version=d['version'],excluded_ids=[])
 elif ident=='Y03':
  svc=w.app.state.media_collections; cid=w.variables['collection_a']; d=svc.get('bench-a',cid)
  svc.patch('bench-a',cid,expected_version=d['version'],run_ids=[w.variables['run_a'],w.variables['extra2_run']])
 else:
  assert '游客' in w.app.state.queries.get_topic('extra2-compare-a').brief
  assert '司机' in w.app.state.queries.get_topic('extra2-compare-b').brief
 evidence={'before':before,'after':w.snapshot(),'variables':w.variables,'final_answer':'地点都是车站。甲是游客，乙是司机。'}
 checks=[_expectation(x,evidence) for x in case['expectations']]
 assert all(x[0] for x in checks),checks
 wrong=deepcopy(evidence)
 if ident=='Y01': next(x for x in wrong['after']['database']['topics'] if x['id']=='extra2-second-3')['brief']='错误'
 elif ident=='Y02': next(x for x in wrong['after']['database']['media_collections'] if x['id']==w.variables['collection_a'])['excluded_ids_json']=json.dumps([w.variables['extra2_item2']])
 elif ident=='Y03': next(x for x in wrong['after']['database']['media_collections'] if x['id']==w.variables['collection_a'])['run_ids_json']=json.dumps([w.variables['extra2_run']])
 else: wrong['final_answer']='都在车站，人物都是游客。'
 assert not all(_expectation(x,wrong)[0] for x in case['expectations'])
 missing=deepcopy(evidence)
 if ident in ('Y01','Y02','Y03'): del missing['after']['database']['topics' if ident=='Y01' else 'media_collections']
 else: del missing['final_answer']
 exp=case['expectations'][0] if ident!='Y04' else case['expectations'][1]
 try: _expectation(exp,missing)
 except MissingEvidence: pass
 else: raise AssertionError('Missing evidence should not pass')
finally: w.close()
'''
        def run(ident):
            with TemporaryDirectory() as temp:
                p=subprocess.run([sys.executable,'-c',code,temp,str(DATA),ident],cwd=ROOT,capture_output=True,text=True,encoding='utf8',errors='replace',timeout=120)
                return ident,p.returncode,p.stderr[-5000:]
        with ThreadPoolExecutor(max_workers=2) as pool:
            for ident,status,error in pool.map(run,self.cases):
                with self.subTest(id=ident): self.assertEqual(status,0,error)

    def test_semantic_gate_handles_unknown(self):
        from creatoros.evaluation.benchmark.scoring import score_case
        for case in self.cases.values():
            ids=[x['id'] for x in case['scoring']['program_checks']]+case['scoring']['critical_checks']
            grade={'checks':[{'id':x,'status':'passed'} for x in ids]}
            for label,status in ((True,'passed'),(False,'failed'),(None,'needs_review')):
                judge={'items':[{'id':case['rubric'][0]['id'],'pass':label,'evidence':'controlled label'}]}
                with self.subTest(id=case['id'],label=label): self.assertEqual(score_case(case,grade,judge)['semantic']['status'],status)

if __name__=='__main__':unittest.main()
