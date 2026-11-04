import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from creatoros.evaluation.benchmark.judge_calibration import REPO, compare, freeze, run, validate_dataset, verify
from creatoros.evaluation.benchmark.semantic_judge import evaluate_judge, scoped_business_oracle


class FakeProvider:
    response = None
    contexts = []
    def __init__(self, **kwargs):
        self.sink = kwargs['sink']
    def complete(self, context):
        self.contexts.append(context.to_request())
        self.sink({'request_id':'test', 'model':'deepseek-flash', 'purpose':'judge',
                   'input_tokens':10, 'output_tokens':2, 'cache_hit_tokens':0, 'cache_miss_tokens':10})
        return SimpleNamespace(content=json.dumps(self.response, ensure_ascii=False))


class JudgeCalibrationTest(unittest.TestCase):
    def test_semantic_judge_uses_shared_prompt_and_temporal(self):
        case = {'id':'B03','rubric':[{'id':'s','question':'明确澄清？'}]}
        payload = {'rubric':case['rubric'], 'temporal_evidence':{}, 'answer':'请说具体对象'}
        FakeProvider.response = {'items':[{'id':'s','pass':True,'evidence':'回答要求澄清'}]}
        result = evaluate_judge(case, payload, [], REPO, lambda x: None, provider_factory=FakeProvider)
        messages, tools = FakeProvider.contexts[-1]
        self.assertIn('仅按本题rubric判断语义', messages[0]['content'])
        self.assertEqual(tools, [])
        self.assertEqual(result['temporal_evidence'], {})
        self.assertTrue(result['items'][0]['pass'])

    def test_scoped_oracle_excludes_foreign_runs_and_unknown_is_not_empty(self):
        state = {'database': {'series':[{'id':'s1','creator_id':'a'},{'id':'s2','creator_id':'b'}],
            'topics':[{'id':'t1','series_id':'s1'},{'id':'t2','series_id':'s2'}],
            'topic_removals':[{'topic_id':'foreign-secret','series_id':'s2','creator_id':'b','status':'deleted'}],
            'content_runs':[{'id':'r1','topic_id':'t1','status':'done','secret':'discard'},
                            {'id':'r2','topic_id':'t2','status':'done'}]}}
        projected = scoped_business_oracle(state, 'a')
        self.assertEqual(projected['runs'], [{'id':'r1','topic_id':'t1','status':'done'}])
        self.assertEqual(projected['removed_topic_ids'], [])
        state['database']['topic_removals'].append({'topic_id':'t1','series_id':'s1','creator_id':None,'status':'deleted'})
        projected = scoped_business_oracle(state, 'a')
        self.assertEqual(projected['removed_topic_ids'], ['t1'])
        self.assertTrue(projected['removals_complete'])
        state['database']['topic_removals'][-1]['creator_id'] = 'b'
        self.assertFalse(scoped_business_oracle(state, 'a')['removals_complete'])
        del state['database']['content_runs']
        self.assertFalse(scoped_business_oracle(state, 'a')['runs_complete'])
        self.assertIsNone(scoped_business_oracle(state, 'a')['runs'])

    def test_missing_and_incorrect_verdict_are_not_pass(self):
        result = compare({'a':True,'b':False,'c':None}, {'items':[{'id':'a','pass':False}, {'id':'b','pass':True}]})
        self.assertEqual([x['classification'] for x in result], ['false_fail','false_pass','missing_response'])

    def test_freeze_requires_paid_flag_and_cannot_repeat(self):
        tmp = REPO / 'tmp'
        tmp.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=tmp) as parent:
            root = Path(parent) / 'trial'
            freeze(root)
            _, data = verify(root)
            self.assertEqual({x['split'] for x in data['samples']}, {'development','holdout'})
            for sample in data['samples']:
                self.assertIn({'role':'assistant', 'content':sample['payload']['answer']}, sample['payload']['conversation'])
            with self.assertRaises(ValueError): run(root, 'development')
            # Test one isolated sample without network while still exercising the exact shared Judge path.
            sample = data['samples'][0]
            FakeProvider.response = {'items':[{'id':key,'pass':value,'evidence':'fixture evidence'} for key,value in sample['expected'].items()]}
            # all development examples do not share the same rubric; mismatch is recorded, never a success
            result = run(root, 'development', allow_paid=True, provider_factory=FakeProvider)
            self.assertTrue(result[0]['checks'][0]['match'])
            self.assertEqual(result[0]['metrics']['cost']['status'], 'unavailable')
            with self.assertRaises(FileExistsError): run(root, 'development', allow_paid=True, provider_factory=FakeProvider)

    def test_rejects_incomplete_expected_labels(self):
        data = {'samples':[{'id':'one','split':'development','case':{'rubric':[{'id':'x'}]},
            'payload':{'rubric':[{'id':'x'}]},'expected':{}}]}
        with self.assertRaises(ValueError): validate_dataset(data)


if __name__ == '__main__': unittest.main()
