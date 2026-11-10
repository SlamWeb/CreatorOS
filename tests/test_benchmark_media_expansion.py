"""Account-scoped collection tasks: real isolated services and oracle counterexamples."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT=Path(__file__).resolve().parents[1]
DATASET=ROOT/'docs/agent-eval/expansion-media-v1/cases.json'

class MediaExpansionTests(unittest.TestCase):
    def test_removed_row_requires_present_unique_identity(self):
        from creatoros.evaluation.benchmark.grader import _expectation, MissingEvidence
        e={'kind':'row_removed','table':'media_collections','id':'A'}
        data={'before':{'database':{'media_collections':[{'id':'A'},{'id':'B'}]}},
              'after':{'database':{'media_collections':[{'id':'B'}]}}}
        self.assertTrue(_expectation(e,data)[0])
        data['after']['database']['media_collections']=[{'id':'A'}]
        self.assertFalse(_expectation(e,data)[0])
        data['before']['database']['media_collections']=[{'id':'B'}]
        with self.assertRaises(MissingEvidence):_expectation(e,data)
        data['before']['database']['media_collections']=[{'id':'A'},{'id':'A'}]
        with self.assertRaises(MissingEvidence):_expectation(e,data)

    def test_collection_list_rejects_wrong_order_and_missing_state(self):
        from creatoros.evaluation.benchmark.grader import _expectation, MissingEvidence
        e={'kind':'collection_list','table':'media_collections','id':'A','field':'order_ids_json','value':['second','first']}
        data={'after':{'database':{'media_collections':[{'id':'A','order_ids_json':'["second", "first"]'}]}}}
        self.assertTrue(_expectation(e,data)[0])
        data['after']['database']['media_collections'][0]['order_ids_json']='["first", "second"]'
        self.assertFalse(_expectation(e,data)[0])
        data['after']['database']['media_collections'][0]['order_ids_json']='not json'
        with self.assertRaises(MissingEvidence):_expectation(e,data)

    def test_missing_and_foreign_source_preconditions_do_not_guess(self):
        from creatoros.evaluation.benchmark.grader import _expectation, MissingEvidence
        import json
        missing={'kind':'row_absent_before','table':'content_runs','id':'missing'}
        data={'before':{'database':{'content_runs':[{'id':'other'}]}}}
        self.assertTrue(_expectation(missing,data)[0])
        data['before']['database']['content_runs'].append({'id':'missing'})
        self.assertFalse(_expectation(missing,data)[0])
        data['before']['database'].pop('content_runs')
        with self.assertRaises(MissingEvidence):_expectation(missing,data)
        check={'kind':'foreign_run_precondition'}
        data={'variables':{'run_b':'r','creator_a':'a','creator_b':'b'},
              'before':{'database':{'content_runs':[{'id':'r','active_revision_number':1}],
                  'content_revisions':[{'content_run_id':'r','revision_number':1,
                                        'production_input_json':json.dumps({'creator_id':'b'})}]}}}
        self.assertTrue(_expectation(check,data)[0])
        data['before']['database']['content_revisions'][0]['production_input_json']=json.dumps({'creator_id':'a'})
        with self.assertRaises(MissingEvidence):_expectation(check,data)

    def test_each_case_against_real_isolated_collection_service(self):
        code=r"""
import json,os,sys
from pathlib import Path
root=Path(sys.argv[1]); ident=sys.argv[2]; dataset=Path(sys.argv[3])
os.environ['DATABASE_URL']='sqlite:///'+(root/'world'/'world.db').as_posix()
from creatoros.evaluation.benchmark.world import BenchmarkWorld
from creatoros.evaluation.benchmark.grader import _expectation
case=next(c for c in json.loads(dataset.read_text(encoding='utf-8'))['cases'] if c['id']==ident)
w=BenchmarkWorld(root/'world',case,None,dataset_path=dataset,eval_root=root/'eval')
try:
 service=w.app.state.media_collections; creator=w.variables['creator_a']; cid=w.variables['collection_a']
 detail=service.get(creator,cid)
 if ident=='M01': service.patch(creator,cid,expected_version=detail['version'],name='精选素材')
 elif ident=='M02': service.delete(creator,cid,expected_version=detail['version'])
 elif ident=='M03': service.create(creator,name='成品精选',request_id='media-test-create',run_ids=[w.variables['run_a']])
 elif ident=='M04': service.patch(creator,cid,expected_version=detail['version'],excluded_ids=[w.variables['collection_item_2']])
 elif ident=='M05': service.patch(creator,cid,expected_version=detail['version'],order_ids=[w.variables['collection_item_2'],w.variables['collection_item_1']])
 elif ident=='M06': service.patch(creator,cid,expected_version=detail['version'],run_ids=[])
 elif ident in ('M07','M08'):
  target='bench-run-missing' if ident=='M07' else w.variables['run_b']
  try: service.patch(creator,cid,expected_version=detail['version'],run_ids=[target])
  except Exception: pass
  else: raise AssertionError('Invalid or foreign source should not be accepted')
 data={'before':w.before,'after':w.snapshot(),'variables':w.variables}
 results=[_expectation(rule,data) for rule in case['expectations']]
 assert all(x[0] for x in results),results
finally:w.close()
"""
        def execute(ident):
            with TemporaryDirectory() as tmp:
                result=subprocess.run([sys.executable,'-c',code,tmp,ident,str(DATASET)],cwd=ROOT,
                                      capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120)
                return ident,result.returncode,result.stderr[-4000:]
        with ThreadPoolExecutor(max_workers=3) as pool:
            for ident,status,error in pool.map(execute,('M01','M02','M03','M04','M05','M06','M07','M08')):
                with self.subTest(case=ident):self.assertEqual(status,0,error)

if __name__=='__main__':unittest.main()
