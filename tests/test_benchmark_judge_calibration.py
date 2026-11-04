import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from creatoros.evaluation.benchmark.judge_calibration import REPO, compare, freeze, run, validate_dataset, verify
from creatoros.evaluation.benchmark.grounding_guard import qualify_grounding_judgments
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

    def test_unverified_research_source_is_neither_automatic_pass_nor_failure(self):
        for original in (True, False):
            item = {'id':'b48-s1', 'pass':original, 'evidence':'a URL was present'}
            result = qualify_grounding_judgments({'id':'B48'}, {'items':[item]})
            self.assertIsNone(result['items'][0]['pass'])
            self.assertEqual(item['pass'], original)

    def test_freeze_requires_paid_flag_and_cannot_repeat(self):
        tmp = REPO / 'tmp'
        tmp.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=tmp) as parent:
            root = Path(parent) / 'trial'
            freeze(root)
            _, data = verify(root)
            self.assertEqual({x['split'] for x in data['samples']}, {'development','holdout'})
            self.assertEqual({x['case']['id'] for x in data['samples']}, {f'B{n:02}' for n in range(1, 51)})
            dev = {x['case']['id'] for x in data['samples'] if x['split'] == 'development'}
            heldout = {x['case']['id'] for x in data['samples'] if x['split'] == 'holdout'}
            self.assertFalse(dev & heldout)
            for sample in data['samples']:
                answer = sample['payload']['answer']
                replies = [t['content'] for t in sample['payload']['conversation'] if t.get('role') == 'assistant']
                self.assertTrue(replies and all(content in answer for content in replies))
                self.assertTrue(sample['source'] and sample['basis'])
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

    def test_resume_never_retries_unknown_claim_but_runs_unclaimed_sample(self):
        with tempfile.TemporaryDirectory(dir=REPO / 'tmp') as parent:
            from creatoros.evaluation.benchmark.judge_calibration import DATASET
            data = json.loads(DATASET.read_text(encoding='utf-8'))
            dev = [x for x in data['samples'] if x['split'] == 'development'][:2]
            heldout = next(x for x in data['samples'] if x['split'] == 'holdout')
            data['samples'] = dev + [heldout]
            dataset = Path(parent) / 'small.json'
            dataset.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            root = Path(parent) / 'run'
            freeze(root, dataset)
            (root / dev[0]['id']).mkdir()
            before = len(FakeProvider.contexts)
            rule = dev[1]['case']['rubric'][0]['id']
            FakeProvider.response = {'items':[{'id':rule,'pass':False,'evidence':'fixture'}]}
            rows = run(root, 'development', allow_paid=True, resume=True, provider_factory=FakeProvider)
            self.assertEqual(len(FakeProvider.contexts) - before, 1)
            self.assertEqual(rows[0]['error']['type'], 'UnresolvedClaim')
            self.assertFalse((root / dev[0]['id'] / 'input.json').exists())
            self.assertTrue((root / dev[1]['id'] / 'result.json').exists())
            with self.assertRaises(FileExistsError):
                run(root, 'development', allow_paid=True, resume=True, provider_factory=FakeProvider)
            self.assertEqual(len(FakeProvider.contexts) - before, 1)

    def test_source_drift_and_conflicting_conversation_rejected(self):
        from creatoros.evaluation.benchmark.judge_calibration import DATASET
        data = json.loads(DATASET.read_text(encoding='utf-8'))
        data['samples'][0]['payload']['conversation'][-1]['content'] = 'invented different reply'
        with self.assertRaisesRegex(ValueError, 'every recorded assistant reply'):
            validate_dataset(data)
        data = json.loads(DATASET.read_text(encoding='utf-8'))
        data['source_dataset_canonical_sha256'] = '0' * 64
        with tempfile.TemporaryDirectory(dir=REPO / 'tmp') as parent:
            dataset = Path(parent) / 'stale.json'
            dataset.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'different case definition'):
                freeze(Path(parent) / 'run', dataset)

    def test_frozen_case_context_cannot_be_replaced_silently(self):
        from creatoros.evaluation.benchmark.judge_calibration import DATASET
        data = json.loads(DATASET.read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory(dir=REPO / 'tmp') as parent:
            for field in ('task', 'intent', 'expected_outcome', 'actual_queries'):
                changed = json.loads(json.dumps(data, ensure_ascii=False))
                changed['samples'][0]['payload'][field] = [] if field in {'task', 'actual_queries'} else 'wrong'
                dataset = Path(parent) / (field + '.json')
                dataset.write_text(json.dumps(changed, ensure_ascii=False), encoding='utf-8')
                with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'differs|differ'):
                    freeze(Path(parent) / ('run-' + field), dataset)

    def test_freeze_saves_source_snapshot_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory(dir=REPO / 'tmp') as parent:
            source = Path(parent) / 'safe_source.py'
            source.write_text('value = 1\n', encoding='utf-8')
            root = Path(parent) / 'frozen'
            with patch('creatoros.evaluation.benchmark.judge_calibration.SOURCES', [source]):
                freeze(root)
                manifest, _ = verify(root)
                name = source.relative_to(REPO).as_posix()
                snapshot = root / 'source-snapshot' / name
                self.assertEqual(snapshot.read_bytes(), source.read_bytes())
                self.assertEqual(manifest['source_snapshot']['file_sha256'][name], manifest['source_sha256'][name])
                snapshot.write_text('value = 2\n', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'snapshot changed'):
                    verify(root)
                snapshot.write_bytes(source.read_bytes())
                verify(root)
                source.write_text('value = 3\n', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'source changed'):
                    verify(root)

    def test_addendum_covers_only_previously_uncovered_rules(self):
        from creatoros.evaluation.benchmark.judge_calibration import ADDENDUM_DATASET, DATASET, CASE_DEFINITIONS
        old = json.loads(DATASET.read_text(encoding='utf-8'))
        addon = json.loads(ADDENDUM_DATASET.read_text(encoding='utf-8'))
        validate_dataset(addon)
        expected_rules = {rule['id'] for case in json.loads(CASE_DEFINITIONS.read_text(encoding='utf-8'))['cases']
                          for rule in case['rubric']}
        old_rules = {rule['id'] for sample in old['samples'] for rule in sample['case']['rubric']}
        addon_rules = {rule['id'] for sample in addon['samples'] for rule in sample['case']['rubric']}
        self.assertEqual(expected_rules - old_rules, addon_rules)
        self.assertFalse(old_rules & addon_rules)
        self.assertFalse({s['id'] for s in old['samples']} & {s['id'] for s in addon['samples']})
        with tempfile.TemporaryDirectory(dir=REPO / 'tmp') as parent:
            root = Path(parent) / 'addendum'
            freeze(root, ADDENDUM_DATASET)
            _, frozen = verify(root)
            self.assertEqual(frozen['dataset_id'], 'benchmark-judge-calibration-v4-addendum')

    def test_cli_addendum_is_explicit_and_run_cannot_change_frozen_dataset(self):
        from contextlib import redirect_stderr
        from io import StringIO
        from creatoros.evaluation.benchmark.judge_calibration import main, DATASET, ADDENDUM_DATASET
        with patch('creatoros.evaluation.benchmark.judge_calibration.freeze') as mocked:
            main(['freeze', '--root', 'tmp/chosen'])
            mocked.assert_called_once_with('tmp/chosen', DATASET)
        with patch('creatoros.evaluation.benchmark.judge_calibration.freeze') as mocked:
            main(['freeze', '--root', 'tmp/chosen', '--dataset', 'v4-addendum'])
            mocked.assert_called_once_with('tmp/chosen', ADDENDUM_DATASET)
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            main(['run', '--root', 'tmp/chosen', '--split', 'development', '--dataset', 'v4-addendum', '--allow-paid'])

    def test_v5_guard_examples_are_explicit_and_use_program_check_ids(self):
        from creatoros.evaluation.benchmark.judge_calibration import PROGRAM_GUARD_DATASET
        from creatoros.evaluation.benchmark.program_evidence_guard import VERSION
        data = json.loads(PROGRAM_GUARD_DATASET.read_text(encoding='utf-8'))
        self.assertEqual(len(data['samples']), 12)
        self.assertEqual({s['case']['id'] for s in data['samples']}, {'B22', 'B24', 'B43', 'B46'})
        with tempfile.TemporaryDirectory(dir=REPO / 'tmp') as parent:
            root = Path(parent) / 'v5'
            freeze(root, PROGRAM_GUARD_DATASET)
            manifest, frozen = verify(root)
            self.assertEqual(manifest['program_evidence_guard_version'], VERSION)
            self.assertEqual(frozen['dataset_id'], 'benchmark-judge-calibration-v5-program-guard')
            manifest['program_evidence_guard_version'] = 'old'
            (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'qualification version'):
                verify(root)

    def test_v5_cli_must_select_new_dataset_explicitly(self):
        from creatoros.evaluation.benchmark.judge_calibration import main, PROGRAM_GUARD_DATASET
        with patch('creatoros.evaluation.benchmark.judge_calibration.freeze') as mocked:
            main(['freeze', '--root', 'tmp/chosen', '--dataset', 'v5-program-guard'])
            mocked.assert_called_once_with('tmp/chosen', PROGRAM_GUARD_DATASET)


if __name__ == '__main__': unittest.main()
