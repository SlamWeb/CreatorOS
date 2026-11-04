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


if __name__ == '__main__': unittest.main()
