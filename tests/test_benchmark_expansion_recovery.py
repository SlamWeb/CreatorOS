"""Recovery fixtures: session-scoped durable plan states, no paid calls."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from creatoros.evaluation.benchmark.expansion_recovery import EVENT, PLAN_ID, check
from creatoros.evaluation.benchmark.grader import MissingEvidence
from creatoros.evaluation.benchmark.manifest import _dataset
ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'docs/agent-eval/expansion-recovery/cases.json'


class RecoveryEvaluationTests(unittest.TestCase):
    def test_versions_require_exact_host_snapshot_and_same_account_session(self):
        plan={'id':PLAN_ID,'creator_id':'bench-a','session_id':'s1','status':'paused','version':1}
        steps=[{'id':PLAN_ID+':collect','plan_id':PLAN_ID,'status':'succeeded'},
               {'id':PLAN_ID+':deliver','plan_id':PLAN_ID,'status':'pending'}]
        control={'name':EVENT,'session_id':'s1','creator_id':'bench-a','initial_plans':[plan], 'initial_steps':steps}
        data={'controls':[control], 'variables':{'creator_a':'bench-a'},
              'before':{'database':{'agent_plans':[],'agent_plan_steps':[]},'files':{}},
              'after':{'database':{'agent_plans':deepcopy([plan]),'agent_plan_steps':deepcopy(steps)},'files':{}}}
        self.assertTrue(check({},data)[0])
        for mutation in ('edited_step','new_plan','dropped_step','created_file'):
            changed=deepcopy(data)
            if mutation=='edited_step': changed['after']['database']['agent_plan_steps'][1]['status']='succeeded'
            if mutation=='new_plan': changed['after']['database']['agent_plans'].append({'id':'extra'})
            if mutation=='dropped_step': changed['after']['database']['agent_plan_steps'].pop()
            if mutation=='created_file': changed['after']['files']['new.txt']='digest'
            with self.subTest(mutation=mutation): self.assertFalse(check({},changed)[0])
        for mutation in ('missing_control','missing_snapshot','wrong_owner','wrong_session','duplicate_control','existing_target'):
            changed=deepcopy(data)
            if mutation=='missing_control': changed.pop('controls')
            if mutation=='missing_snapshot': changed['after']['database'].pop('agent_plan_steps')
            if mutation=='wrong_owner': changed['variables']['creator_a']='other'
            if mutation=='wrong_session': changed['controls'][0]['session_id']='other'
            if mutation=='duplicate_control': changed['controls'].append(deepcopy(control))
            if mutation=='existing_target': changed['before']['database']['agent_plans']=[plan]
            with self.subTest(mutation=mutation),self.assertRaises(MissingEvidence): check({},changed)

    def test_suite_contract(self):
        dataset=_dataset(DATA)
        self.assertEqual([row['id'] for row in dataset['cases']], ['W01','W02','W03','W04'])
        for row in dataset['cases']:
            self.assertEqual(row['steps'][1], {'kind':'event','name':EVENT})
            self.assertEqual(row['expectations'][0]['kind'],'recovery_control_unchanged')
            self.assertEqual(row['trajectory']['required'][0]['action'],'get')

    def test_host_events_seed_actual_account_session_and_plan_api(self):
        script=r"""
import json,os,sys
from pathlib import Path
root=Path(sys.argv[1]); name=sys.argv[2]; path=Path(sys.argv[3])
os.environ['DATABASE_URL']='sqlite:///'+(root/'world'/'world.db').as_posix()
from creatoros.evaluation.benchmark.world import BenchmarkWorld
from creatoros.evaluation.benchmark.expansion_recovery import host_event,check,PLAN_ID
case=next(c for c in json.loads(path.read_text(encoding='utf8'))['cases'] if c['id']==name)
w=BenchmarkWorld(root/'world',case,None,dataset_path=path,eval_root=root/'eval')
try:
 sid=w.app.state.chat.create('bench-a')['id']; other=w.app.state.chat.create('bench-b')['id']
 try: host_event(w,'seed_recovery_plan',other)
 except ValueError: pass
 else: raise AssertionError('wrong account should not receive fixture')
 receipt=host_event(w,'seed_recovery_plan',sid)
 plan=w.app.state.agent_plans.get(sid,PLAN_ID)
 from fastapi.testclient import TestClient
 with TestClient(w.app) as client:
  url=f'/api/agent/sessions/{sid}/plans/{PLAN_ID}'
  accessible=client.get(url,headers={'x-creatoros-agent-session':sid})
  assert accessible.status_code==200, (accessible.status_code,accessible.text)
  assert accessible.json()['id']==PLAN_ID
  denied=client.get(url,headers={'x-creatoros-agent-session':other})
  assert denied.status_code==403, (denied.status_code,denied.text)
 expected={'W01':('paused',['succeeded','pending']),'W02':('needs_attention',['failed','blocked']),
           'W03':('needs_attention',['succeeded','unknown']),'W04':('completed',['succeeded'])}
 assert (plan['status'],[x['status'] for x in plan['steps']])==expected[name],plan
 try: w.app.state.agent_plans.get(other,PLAN_ID)
 except Exception: pass
 else: raise AssertionError('cross-session plan leaked')
 after=w.snapshot(); evidence={'controls':receipt['controls'],'variables':w.variables,'before':w.before,'after':after}
 assert check({},evidence)[0]
 assert all(r['id'] != PLAN_ID for r in w.before['database']['agent_plans'])
 try: host_event(w,'seed_recovery_plan',sid)
 except ValueError: pass
 else: raise AssertionError('fixture event duplicated')
finally: w.close()
"""
        def run(name):
            with TemporaryDirectory() as tmp:
                out=subprocess.run([sys.executable,'-c',script,tmp,name,str(DATA)],cwd=ROOT,
                    capture_output=True,text=True,encoding='utf8',errors='replace',timeout=120)
                return name,out.returncode,out.stderr[-3000:]
        with ThreadPoolExecutor(max_workers=3) as pool:
            for name,code,error in pool.map(run,['W01','W02','W03','W04']):
                with self.subTest(name=name): self.assertEqual(code,0,error)

if __name__=='__main__': unittest.main()
