"""Natural-v2 registration, B10 wording and backwards compatibility."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from creatoros.evaluation.benchmark import manifest, natural_suite
from creatoros.evaluation.benchmark.scoring import score_case

REPO=Path(__file__).resolve().parents[1]

class NaturalV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v1=json.loads((REPO/'docs/agent-eval/natural-v1/cases.json').read_text(encoding='utf8'))
        cls.v2=json.loads((REPO/'docs/agent-eval/natural-v2/cases.json').read_text(encoding='utf8'))
        cls.parent=(REPO/'docs/agent-eval/benchmark-v2/cases.json').read_bytes()

    def test_registered_versions_keep_old_definition_and_exact_parent(self):
        self.assertEqual(self.v1, natural_suite.build_document(self.parent))
        self.assertEqual(self.v2, natural_suite.build_document_v2(self.parent))
        self.assertTrue(natural_suite.validate_natural_document(self.v1))
        self.assertTrue(natural_suite.validate_natural_document(self.v2))
        self.assertNotEqual(self.v1['dataset_id'], self.v2['dataset_id'])
        self.assertEqual(self.v2['evaluation_revision']['natural_grader_version'],'benchmark-natural-v2-intent')
        self.assertEqual(self.v2['revision']['natural_v2']['parent_canonical_sha256'],natural_suite.canonical_digest(self.v1))

    def test_b10_does_not_require_repeating_user_attribution(self):
        old=next(x for x in self.v1['cases'] if x['id']=='B10')
        new=next(x for x in self.v2['cases'] if x['id']=='B10')
        self.assertEqual(new['steps'],old['steps'])
        self.assertEqual(new['scoring'],old['scoring'])
        rubric=next(x for x in new['rubric'] if x['id']=='b10-s2')['question']
        self.assertIn('未重复该来源不扣分',rubric)
        self.assertIn('没有声称Agent自己创建',rubric)
        self.assertIn('旧目录',rubric)

    def test_both_revisions_keep_explicit_subset_and_reject_tampering(self):
        for doc in (self.v1,self.v2):
            with self.subTest(dataset=doc['dataset_id']):
                with self.assertRaises(ValueError): natural_suite.validate_selection(doc,None)
                with self.assertRaises(ValueError): natural_suite.validate_selection(doc,['B50'])
                natural_suite.validate_selection(doc,['B10','B12'])
                changed=json.loads(json.dumps(doc));changed['cases'][9]['rubric'][1]['question']='arbitrary'
                with self.assertRaises(ValueError):natural_suite.validate_natural_document(changed)

    def test_actual_manifest_freezes_both_versions_independently(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            source=root/'source.py';source.write_text('VALUE=1\n',encoding='utf8')
            with patch.object(manifest,'PROJECT_ROOT',root), patch.object(manifest,'_git_state',return_value={'sha':'test'}):
                for name,doc in (('v1',self.v1),('v2',self.v2)):
                    dataset=root/(name+'.json');dataset.write_text(json.dumps(doc,ensure_ascii=False),encoding='utf8')
                    target=root/name/'manifest.json'
                    result=manifest.freeze_manifest(target,dataset,pricing={'currency':'USD'},
                        model_configuration={'retries':0},case_ids=['B10','B12'],source_paths=[source])
                    self.assertEqual(result['dataset_id'],doc['dataset_id'])
                    self.assertTrue(manifest.verify_manifest(target)['valid'])

    def test_b10_can_accept_correct_no_attribution_but_not_judge_failure(self):
        task=next(x for x in self.v2['cases'] if x['id']=='B10')
        checks=[{'id':x['id'],'status':'passed'} for x in task['scoring']['program_checks']]
        checks += [{'id':x,'status':'passed'} for x in task['scoring']['critical_checks']]
        judged={'items':[{'id':x['id'],'pass':True,'evidence':'latest list correct; no claim to have added it'} for x in task['rubric']]}
        self.assertEqual(score_case(task,{'checks':checks},judged)['semantic']['status'],'passed')
        judged['items'][-1]['pass']=False
        self.assertEqual(score_case(task,{'checks':checks},judged)['semantic']['status'],'failed')

    def test_b12_semantics_cannot_hide_wrong_pair_behind_correct_titles(self):
        task=next(x for x in self.v2['cases'] if x['id']=='B12')
        rule=next(x for x in task['rubric'] if x['id']=='b12-s1')
        self.assertIn('各自对应',rule['question'])
        self.assertIn('没有错配',rule['question'])
        self.assertIn('证据不足待复核',rule['question'])
        checks=[{'id':x['id'],'status':'passed'} for x in task['scoring']['program_checks']]
        checks += [{'id':x,'status':'passed'} for x in task['scoring']['critical_checks']]
        # All three titles and row counts are right, but the first two briefs
        # were swapped: a false semantic judgment must block strict success.
        items=[{'id':'b12-s1','pass':False,'evidence':'rise/raise title contains accept/except brief, and vice versa'},
               {'id':'b12-s2','pass':True,'evidence':'three correctly reported queued entries'}]
        scores=score_case(task,{'checks':checks},{'items':items})
        self.assertEqual(scores['program']['status'],'passed')
        self.assertEqual(scores['semantic']['status'],'failed')
        self.assertEqual(scores['critical_status'],'failed')
        items[0]['pass']=None
        scores=score_case(task,{'checks':checks},{'items':items})
        self.assertEqual(scores['semantic']['status'],'needs_review')
        self.assertEqual(scores['critical_status'],'needs_review')

if __name__=='__main__': unittest.main()
