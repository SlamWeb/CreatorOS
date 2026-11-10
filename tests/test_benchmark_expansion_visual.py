"""Visual identity preconditions and conservative Judge gates; no paid calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from PIL import Image
from creatoros.evaluation.benchmark.expansion_visual_tasks import collect_visual, preconditions, qualify, VISUAL_VERSION

ROOT=Path(__file__).resolve().parents[1]
DATASET=ROOT/'docs/agent-eval/expansion-visual/cases.json'


class VisualExpansionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc=json.loads(DATASET.read_text(encoding='utf-8'))
        cls.cases={x['id']:x for x in cls.doc['cases']}

    def test_valid_suite_and_semantic_unknown_on_missing_preconditions(self):
        from creatoros.evaluation.benchmark.manifest import _dataset
        self.assertEqual(len(_dataset(DATASET)['cases']),5)
        for cid,case in self.cases.items():
            raw={'items':[{'id':cid.lower()+'-s1','pass':True,'evidence':'asserted'}]}
            with self.subTest(case=cid):
                self.assertIsNone(qualify(case,raw,{'checks':[]})['items'][0]['pass'])
                self.assertIs(raw['items'][0]['pass'],True)
                if cid!='I01':
                    checked={'checks':[{'id':'visual_preconditions','status':'passed'}]}
                    self.assertIs(qualify(case,raw,checked)['items'][0]['pass'],True)
                    checked['checks'].append({'id':'visual_preconditions','status':'passed'})
                    self.assertIsNone(qualify(case,raw,checked)['items'][0]['pass'])

    def test_two_visual_bindings_require_distinct_bytes_and_both_agent_reads(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);base='world-producer-skills/working/sk';assets=root/base/'assets';assets.mkdir(parents=True)
            files={};calls=[];messages=[]
            for n,color in [(1,'blue'),(2,'red')]:
                file=assets/f'reference-0{n}.png';Image.new('RGB',(8,8),color).save(file)
                sha=hashlib.sha256(file.read_bytes()).hexdigest();rel=f'{base}/assets/{file.name}';files[rel]=sha
                url=f'/api/agent/sessions/session/attachments/{sha}';callid=f'call-{n}'
                calls.append({'tool':'read_image','is_error':False,'arguments':{'skill_id':'sk','path':f'assets/{file.name}'},
                              'raw_result':json.dumps({'attachment':{'id':sha,'url':url}}),
                              'turn_index':1,'turn_id':'turn','tool_call_id':callid})
                messages.extend([{'role':'tool','tool_call_id':callid,'content':json.dumps({'attachment':{'id':sha,'url':url}})},
                                 {'role':'user','content':[{'type':'image_ref','attachment':{'id':sha,'url':url}}]}])
            e={'variables':{'skill_a':'sk'},'before':{'files':files},'skill_paths':{'sk':base},'calls':calls,
               'browser':{'session_id':'session','turns':[{'session_id':'session','turn_id':'turn'}]},
               'requests':[{'purpose':'agent','error':None,'elapsed_seconds':1,'context':{'messages':messages}}]}
            out=collect_visual(root,e);self.assertEqual(out['status'],'complete');self.assertTrue(out['agent_read_verified'])
            judge={'visual_evidence':{**out,'sent_ids':['image-1','image-2']},'items':[{'id':'i01-s1','pass':True,'evidence':'labels checked'}]}
            case=self.cases['I01'];self.assertIs(qualify(case,judge,{})['items'][0]['pass'],True)
            bad=deepcopy(judge);bad['visual_evidence']['bindings'][1]['skill_id']='different-skill'
            self.assertIsNone(qualify(case,bad,{})['items'][0]['pass'])
            bad=deepcopy(judge);bad['visual_evidence']['sent_ids']=['image-2','image-1']
            self.assertIsNone(qualify(case,bad,{})['items'][0]['pass'])
            e['calls']=e['calls'][:1];out=collect_visual(root,e);self.assertFalse(out['agent_read_verified'])
            judge['visual_evidence']={**out,'sent_ids':['image-1','image-2']}
            self.assertIsNone(qualify(case,judge,{})['items'][0]['pass'])
            e['calls']=calls; (assets/'reference-02.png').write_bytes((assets/'reference-01.png').read_bytes())
            self.assertEqual(collect_visual(root,e)['status'],'needs_review')

    def test_missing_attachment_precondition_checks_session_and_turn(self):
        e={'variables':{},'before':{'database':{'topics':[]},'files':{}},'browser':{'session_id':'s',
           'turns':[{'session_id':'s','turn_id':'t','accepted':{'id':'s','entries':[{'kind':'user','turn_id':'t'}]}}]}}
        self.assertEqual(preconditions('.',self.cases['I02'],e)['status'],'passed')
        wrong=deepcopy(e);wrong['browser']['turns'][0]['accepted']['entries'][0]['attachments']=[{'id':'x'}]
        self.assertEqual(preconditions('.',self.cases['I02'],wrong)['status'],'needs_review')
        wrong=deepcopy(e);wrong['browser']['turns'][0]['session_id']='other'
        self.assertEqual(preconditions('.',self.cases['I02'],wrong)['status'],'needs_review')

    def test_real_world_run_revision_preconditions_and_skill_asset(self):
        code=r"""
import json,os,sys
from pathlib import Path
root=Path(sys.argv[1]);path=Path(sys.argv[2]);os.environ['DATABASE_URL']='sqlite:///'+(root/'world'/'world.db').as_posix()
from creatoros.evaluation.benchmark.world import BenchmarkWorld
from creatoros.evaluation.benchmark.expansion_visual_tasks import seed,preconditions
cases={c['id']:c for c in json.loads(path.read_text(encoding='utf-8'))['cases']}
w=BenchmarkWorld(root/'world',cases['I03'],None,dataset_path=path,eval_root=root/'eval')
try:
 seed(w,cases['I03']); e={'variables':w.variables,'before':w.snapshot(),'skill_paths':w.skill_paths}
 for cid in ('I03','I04','I05'):
  assert preconditions(w.root,cases[cid],e)['status']=='passed',cid
 e['variables']=dict(w.variables);e['variables']['visual_revision_b']=w.variables['visual_revision_a']
 assert preconditions(w.root,cases['I04'],e)['status']=='needs_review'
 del e['before']['database']['content_revisions']
 assert preconditions(w.root,cases['I05'],e)['status']=='needs_review'
finally:w.close()
"""
        with TemporaryDirectory() as temp:
            p=subprocess.run([sys.executable,'-c',code,temp,str(DATASET)],cwd=ROOT,capture_output=True,
                             text=True,encoding='utf-8',errors='replace',timeout=120)
            self.assertEqual(p.returncode,0,p.stderr[-4000:])

if __name__=='__main__':unittest.main()
