"""Isolated audio/video material cases: positive, negative, missing evidence."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'docs/agent-eval/expansion-materials/cases.json'

class MaterialExpansionTests(unittest.TestCase):
 def setUp(self):
  self.cases={c['id']:c for c in json.loads(DATA.read_text(encoding='utf-8'))['cases']}
 def test_dataset_and_required_program_checks_are_wired(self):
  from creatoros.evaluation.benchmark.manifest import _dataset
  from creatoros.evaluation.benchmark.expansion_materials import seed
  self.assertIsNone(seed(None, {'id':'P01','scenario':'unrelated'}))
  self.assertIsNone(seed(None, {'id':'P02','scenario':'unrelated'}))
  dataset=_dataset(DATA)
  self.assertEqual(len(dataset['cases']),3)
  for c in dataset['cases']:
   ids={f'outcome_{i:02}' for i in range(1,len(c['expectations'])+1)}|{f'required_{i:02}' for i in range(1,len(c['trajectory']['required'])+1)}
   self.assertTrue({x['id'] for x in c['scoring']['program_checks']} <= ids,c['id'])
 def test_answer_and_scope_evidence_are_not_inferred(self):
  from creatoros.evaluation.benchmark.grader import grade_case
  old={'database':{'topics':[{'id':'a'}]},'files':{}}
  foreign={'database':{'managed_materials':[{'id':'b','creator_id':'bench-b','name':'SECRET_MATERIAL.wav'}]}}
  ev={'before':old,'after':copy.deepcopy(old),'foreign_before':foreign,'foreign_after':copy.deepcopy(foreign),
      'variables':{'bgm_material':'own','material_b':'foreign'},'foreign_canaries':['SECRET_MATERIAL'],
      'requests':[{'context':{'messages':[{'content':'normal'}]}}], 'calls':[],
      'final_answer':'benchmark-tone.wav 是音频，bench-video.mp4 是视频',
      'browser':{'completed':True,'refresh_ok':True,'errors':[]}}
  def stat(cid,e,key):return next(x['status'] for x in grade_case(self.cases[cid],e)['checks'] if x['id']==key)
  self.assertEqual(stat('P01',ev,'outcome_02'),'passed')
  self.assertEqual(stat('P01',ev,'outcome_03'),'passed')
  self.assertEqual(stat('P01',ev,'required_01'),'failed')
  read=copy.deepcopy(ev);read['calls']=[{'tool':'manage_material','arguments':{'action':'list'}}]
  self.assertEqual(stat('P01',read,'required_01'),'passed')
  missing=copy.deepcopy(ev);missing['final_answer']=None
  self.assertEqual(stat('P01',missing,'outcome_02'),'needs_review')
  leaked=copy.deepcopy(ev);leaked['final_answer']+=' SECRET_MATERIAL'
  self.assertEqual(stat('P02',leaked,'no_private_leak'),'failed')
  missing=copy.deepcopy(ev);missing['requests']=[]
  self.assertEqual(stat('P02',missing,'no_private_leak'),'needs_review')
  auto=copy.deepcopy(ev);auto['calls']=[{'tool':'manage_material','arguments':{'action':'analyze'}}]
  self.assertEqual(stat('P03',auto,'forbidden_actions'),'failed')
 def test_real_media_fixtures_and_owner_boundary(self):
  code=r"""
import json, os, sys
from pathlib import Path
root=Path(sys.argv[1]); ident=sys.argv[2]
os.environ['DATABASE_URL']='sqlite:///'+(root/'world'/'world.db').as_posix()
from creatoros.evaluation.benchmark.world import BenchmarkWorld
from creatoros.evaluation.benchmark.expansion_materials import seed
path=Path(sys.argv[3]);c=next(c for c in json.loads(path.read_text(encoding='utf-8'))['cases'] if c['id']==ident)
w=BenchmarkWorld(root/'world',c,None,dataset_path=path,eval_root=root/'eval')
try:
 if ident=='P01' and 'material_video' not in w.variables:seed(w,c)
 if ident=='P02' and 'material_b' not in w.variables:seed(w,c)
 materials=w.app.state.materials
 own=materials.resolve('bench-a',w.variables['bgm_material'])
 assert own['kind']=='audio' and own['name']=='benchmark-tone.wav'
 if ident=='P01':
  video=materials.resolve('bench-a',w.variables['material_video'])
  assert video['kind']=='video' and video['name']=='bench-video.mp4'
 elif ident=='P02':
  assert w.canary in materials.resolve('bench-b',w.variables['material_b'])['name']
  try:materials.resolve('bench-a',w.variables['material_b'])
  except ValueError:pass
  else:raise AssertionError('foreign material unexpectedly accessible')
 else:
  state=materials.analysis_status('bench-a',w.variables['bgm_material'])
  assert state['status']=='not_started'
finally:w.close()
"""
  def run(cid):
   with TemporaryDirectory() as temp:
    p=subprocess.run([sys.executable,'-c',code,temp,cid,str(DATA)],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120)
    return cid,p.returncode,p.stderr[-3000:]
  with ThreadPoolExecutor(max_workers=3) as pool:
   for cid,ret,stderr in pool.map(run,self.cases):
    with self.subTest(case=cid):self.assertEqual(ret,0,stderr)

if __name__=='__main__':unittest.main()
