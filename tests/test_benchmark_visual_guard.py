from copy import deepcopy
import unittest
from creatoros.evaluation.benchmark.visual_guard import qualify_visual_judgments


class VisualGuardTests(unittest.TestCase):
    def test_positive_visual_verdict_is_unknown_without_binding(self):
        judge = {'items':[{'id':'b42-s1','pass':True,'evidence':'三幅图正确'},
                          {'id':'b42-s2','pass':True,'evidence':'时间重叠'}]}
        original = deepcopy(judge)
        revised = qualify_visual_judgments({'id':'B42'}, judge)
        self.assertIsNone(revised['items'][0]['pass'])
        self.assertTrue(revised['items'][1]['pass'])
        self.assertEqual(judge, original)

    def test_negative_visual_verdict_also_needs_binding(self):
        judge = {'items':[{'id':'b42-s1','pass':False,'evidence':'视觉不符'}]}
        self.assertIsNone(qualify_visual_judgments({'id':'B42'}, judge)['items'][0]['pass'])
        self.assertFalse(judge['items'][0]['pass'])

    def test_existing_unknown_is_not_changed(self):
        judge = {'items':[{'id':'b42-s1','pass':None,'evidence':'缺证据'}]}
        self.assertEqual(qualify_visual_judgments({'id':'B42'}, judge), judge)

    def test_same_id_in_other_case_is_not_qualified(self):
        judge = {'items':[{'id':'b42-s1','pass':True,'evidence':'x'}]}
        self.assertEqual(qualify_visual_judgments({'id':'B03'}, judge), judge)

    def test_only_complete_role_bound_and_sent_visuals_allow_assessment(self):
        judge = {'items':[{'id':'b42-s1','pass':True,'evidence':'三幅图'}],
            'visual_evidence':{'version':'visual-bindings-v1','case_id':'B42','status':'complete',
                'bindings':[{'id':f'image-{n}','role':'run_image', 'sha256':'a'*64} for n in (1,2,3)],
                'sent_ids':['image-1','image-2','image-3']}}
        self.assertTrue(qualify_visual_judgments({'id':'B42'}, judge)['items'][0]['pass'])
        for key, bad in [('sent_ids',['image-1','image-2']),('case_id','B41'),('version','old')]:
            changed = deepcopy(judge)
            changed['visual_evidence'][key] = bad
            self.assertIsNone(qualify_visual_judgments({'id':'B42'}, changed)['items'][0]['pass'])
        changed = deepcopy(judge)
        changed['visual_evidence']['bindings'][0]['role'] = None
        self.assertIsNone(qualify_visual_judgments({'id':'B42'}, changed)['items'][0]['pass'])

    def test_read_evidence_is_distinct_from_judge_image_input(self):
        judge = {'items':[{'id':'b29-s1','pass':True,'evidence':'图符合'},
                          {'id':'b29-s2','pass':True,'evidence':'读图'}],
                 'visual_evidence':{'version':'visual-bindings-v1','case_id':'B29','status':'complete',
                     'bindings':[{'id':'image-1','role':'skill_reference','sha256':'a'*64}], 'sent_ids':['image-1'],
                     'agent_read_verified':False}}
        result = qualify_visual_judgments({'id':'B29'},judge)
        self.assertTrue(result['items'][0]['pass'])
        self.assertIsNone(result['items'][1]['pass'])

    def test_rsi_trial_and_reference_must_both_be_sent(self):
        judge = {'items':[{'id':'b04-s1','pass':True,'evidence':'试产背景简洁'}],
                 'visual_evidence':{'version':'visual-bindings-v1','case_id':'B04','status':'complete',
                   'bindings':[{'id':'image-1','role':'rsi_reference','sha256':'a'*64}, {'id':'image-2','role':'rsi_trial','sha256':'b'*64}],
                   'sent_ids':['image-1','image-2']}}
        self.assertTrue(qualify_visual_judgments({'id':'B04'}, judge)['items'][0]['pass'])
        judge['visual_evidence']['sent_ids'].pop()
        self.assertIsNone(qualify_visual_judgments({'id':'B04'}, judge)['items'][0]['pass'])


if __name__ == '__main__': unittest.main()
