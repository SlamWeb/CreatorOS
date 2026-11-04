"""Visual identity, byte matching and ambiguity checks; no model calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
import subprocess
import sys

from creatoros.evaluation.benchmark.visual_evidence import (
    UnavailableVisualEvidence, candidate_attachment_evidence, collect_visual_evidence,
    load_visual_attachments,
)
from tests.test_benchmark_oracle_revision import candidate_evidence, FAMILY, SELECTED, ROOT


def digest(data):
    return hashlib.sha256(data).hexdigest()


class VisualEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.files = {}
        self.put('inputs/reference.png', b'reference')
        self.sha = digest(b'reference')
        self.data = {'variables': {'creator_a':'A', 'topic_a':'T', 'skill_a':'S', 'run_a':'R'},
                     'before': {'files':dict(self.files), 'database':{'content_runs':[]}},
                     'after': {'files':{}, 'database':{'content_runs':[], 'content_revisions':[], 'production_tasks':[]}},
                     'skill_paths':{},
                     'browser':{'session_id':'s1','turns':[{'session_id':'s1','turn_id':'turn1',
                         'accepted':{'id':'s1','entries':[{'kind':'user','turn_id':'turn1','attachments':[{'id':self.sha}]}]}}]}}

    def put(self, path, value):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(value)
        self.files[path] = digest(value)

    def produced(self, count=3):
        for n in range(count):
            run, task, rev = f'R{n}', f'T{n}', f'V{n}'
            path = f'outputs/{run}/image.png'
            self.put(path, f'image-{n}'.encode())
            self.data['after']['database']['content_runs'].append(
                {'id':run,'topic_id':'T','active_revision_number':1,'task_id':task})
            self.data['after']['database']['content_revisions'].append({
                'id':rev,'content_run_id':run,'revision_number':1,'artifact_directory':str(self.root / f'outputs/{run}'),
                'production_input_json':json.dumps({'creator_id':'A','task_id':task,'topic_id':'T','series_id':'S',
                    'topic_title':f'scene-{n}','topic_brief':f'brief-{n}'}),
                'validation_json':json.dumps({'images':[{'path':'image.png','sha256':self.files[path], 'order':1}]})})
            self.data['after']['database']['production_tasks'].append({'id':task,'topic_id':'T','title':f'scene-{n}','brief':f'brief-{n}',
                'definition_json':json.dumps({'creator_id':'A','series_id':'S'})})
        self.data['after']['files'] = dict(self.files)

    def test_user_attachment_receipt_and_file_are_bound(self):
        result = collect_visual_evidence(self.root, {'id':'B44'}, self.data)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['bindings'][0]['role'], 'user_attachment')
        self.assertEqual(load_visual_attachments(self.root, result)[0][1], b'reference')
        self.data['browser']['turns'][0]['accepted']['entries'][0]['attachments'][0]['id'] = '0'*64
        self.assertEqual(collect_visual_evidence(self.root, {'id':'B44'}, self.data)['status'], 'needs_review')

    def test_user_attachment_requires_consistent_session_and_turn_ids(self):
        for field in ('root_session', 'turn_session', 'accepted_session', 'turn_id', 'entry_turn'):
            data = deepcopy(self.data)
            browser = data['browser']; turn = browser['turns'][0]
            if field == 'root_session': browser['session_id'] = 'different'
            if field == 'turn_session': turn['session_id'] = 'different'
            if field == 'accepted_session': turn['accepted']['id'] = 'different'
            if field == 'turn_id': del turn['turn_id']
            if field == 'entry_turn': turn['accepted']['entries'][0]['turn_id'] = 'different'
            with self.subTest(field=field):
                self.assertEqual(collect_visual_evidence(self.root, {'id':'B44'}, data)['status'], 'needs_review')

    def test_run_images_map_individually_to_active_revision_and_task(self):
        self.produced()
        result = collect_visual_evidence(self.root, {'id':'B42'}, self.data)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual([(x['run_id'], x['revision_id'], x['task_title']) for x in result['bindings']],
                         [('R0','V0','scene-0'),('R1','V1','scene-1'),('R2','V2','scene-2')])
        self.data['after']['database']['production_tasks'][0]['title'] = 'later edited title'
        self.data['after']['database']['production_tasks'][0]['brief'] = 'later edited brief'
        frozen = collect_visual_evidence(self.root, {'id':'B42'}, self.data)
        self.assertEqual((frozen['bindings'][0]['task_title'], frozen['bindings'][0]['task_brief']), ('scene-0','brief-0'))
        self.data['after']['database']['content_runs'].reverse()
        reordered = collect_visual_evidence(self.root, {'id':'B42'}, self.data)
        self.assertEqual([(x['run_id'], x['task_id'], x['task_title']) for x in reordered['bindings']],
                         [('R2','T2','scene-2'),('R1','T1','scene-1'),('R0','T0','scene-0')])
        self.data['after']['database']['content_revisions'][0]['revision_number'] = 2
        self.assertEqual(collect_visual_evidence(self.root, {'id':'B42'}, self.data)['status'], 'needs_review')

    def test_tampered_bytes_and_wrong_account_never_qualify(self):
        self.produced(1)
        result = collect_visual_evidence(self.root, {'id':'B41'}, self.data)
        self.assertEqual(result['status'], 'complete')
        (self.root / 'outputs/R0/image.png').write_bytes(b'tampered')
        with self.assertRaises(UnavailableVisualEvidence):
            load_visual_attachments(self.root, result)
        row = self.data['after']['database']['content_revisions'][0]
        row['production_input_json'] = json.dumps({'creator_id':'B','task_id':'T0'})
        self.assertEqual(collect_visual_evidence(self.root, {'id':'B41'}, self.data)['status'], 'needs_review')

    def test_multiple_runs_must_belong_to_distinct_tasks_and_same_owner_topic(self):
        for case_id, count in (('B42', 3), ('B49', 2)):
            with self.subTest(case=case_id), TemporaryDirectory() as root:
                old_root, old_files, old_data = self.root, self.files, self.data
                self.root = Path(root).resolve()
                self.files = {}
                self.data = {'variables': {'creator_a':'A', 'topic_a':'T'},
                    'before': {'files':{}, 'database':{'content_runs':[]}},
                    'after': {'files':{}, 'database':{'content_runs':[], 'content_revisions':[], 'production_tasks':[]}}}
                try:
                    self.produced(count)
                    self.assertEqual(collect_visual_evidence(self.root, {'id':case_id}, self.data)['status'], 'complete')
                    same = deepcopy(self.data)
                    for run in same['after']['database']['content_runs']:
                        run['task_id'] = 'T0'
                    for revision in same['after']['database']['content_revisions']:
                        frozen = json.loads(revision['production_input_json']); frozen['task_id'] = 'T0'
                        revision['production_input_json'] = json.dumps(frozen)
                    self.assertEqual(collect_visual_evidence(self.root, {'id':case_id}, same)['status'], 'needs_review')
                    for mutation in ('wrong_topic','wrong_creator','wrong_series','missing_definition','missing_frozen_brief'):
                        altered = deepcopy(self.data)
                        task = altered['after']['database']['production_tasks'][0]
                        if mutation == 'wrong_topic': task['topic_id'] = 'OTHER'
                        elif mutation == 'missing_definition': del task['definition_json']
                        elif mutation == 'missing_frozen_brief':
                            revision = altered['after']['database']['content_revisions'][0]
                            frozen = json.loads(revision['production_input_json']); del frozen['topic_brief']
                            revision['production_input_json'] = json.dumps(frozen)
                        else:
                            definition = json.loads(task['definition_json'])
                            definition['creator_id' if mutation == 'wrong_creator' else 'series_id'] = 'OTHER'
                            task['definition_json'] = json.dumps(definition)
                        self.assertEqual(collect_visual_evidence(self.root, {'id':case_id}, altered)['status'], 'needs_review')
                finally:
                    self.root, self.files, self.data = old_root, old_files, old_data

    def test_reference_and_identical_result_remain_distinct_roles(self):
        self.produced(1)
        after = self.data['after']['database']
        run, revision = after['content_runs'][0], after['content_revisions'][0]
        run['id'], run['task_id'] = 'R', None
        revision['content_run_id'] = 'R'
        revision['production_input_json'] = json.dumps({'creator_id':'A'})
        self.put('outputs/R0/second.png', b'second')
        self.put('outputs/R0/image.png', b'reference')
        revision['validation_json'] = json.dumps({'images':[{'path':'image.png','sha256':self.sha,'order':1},
            {'path':'second.png','sha256':self.files['outputs/R0/second.png'],'order':2}]})
        self.data['before']['database'] = after
        self.data['before']['files'] = dict(self.files)
        result = collect_visual_evidence(self.root, {'id':'B45'}, self.data)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual([x['role'] for x in result['bindings']], ['user_attachment','run_image','run_image'])
        self.assertEqual(result['bindings'][0]['sha256'], result['bindings'][1]['sha256'])
        self.assertNotEqual(result['bindings'][0]['id'], result['bindings'][1]['id'])
        calls, receipt_messages, image_parts = [], [], []
        for binding in [row for row in result['bindings'] if row['role']=='run_image']:
            call_id = f'call-{binding["image_order"]}'
            sha = binding['sha256']; url = f'/api/agent/sessions/s1/attachments/{sha}'
            attachment = {'id':sha, 'url':url}
            calls.append({'tool':'read_image','is_error':False,'turn_index':1,'turn_id':'turn1','tool_call_id':call_id,
                'arguments':{'run_id':binding['run_id'],'revision_id':binding['revision_id'],'image_index':binding['image_order']},
                'raw_result':json.dumps({'attachment':attachment})})
            receipt_messages.append({'role':'tool','tool_call_id':call_id,'content':json.dumps({'attachment':attachment})})
            image_parts.append({'type':'image_ref','attachment':attachment})
        self.data['calls'] = calls
        self.data['requests'] = [{'purpose':'agent','error':None,'elapsed_seconds':1,
            'context':{'messages':[{'role':'user','content':[image_parts[0]]},*receipt_messages]}}]
        # An earlier user reference with the same SHA must not prove the
        # later run-image role was present in the Agent's model input.
        self.assertFalse(collect_visual_evidence(self.root, {'id':'B45'}, self.data)['agent_read_verified'])
        self.data['requests'][0]['context']['messages'].append({'role':'user','content':image_parts})
        self.assertTrue(collect_visual_evidence(self.root, {'id':'B45'}, self.data)['agent_read_verified'])

        # Raw manifest order can differ from visual page order.
        validation = json.loads(revision['validation_json'])
        validation['images'].reverse()
        revision['validation_json'] = json.dumps(validation)
        result = collect_visual_evidence(self.root, {'id':'B45'}, self.data)
        self.assertEqual([row['image_order'] for row in result['bindings'] if row['role']=='run_image'], [1,2])
        self.assertEqual([content for row, content in load_visual_attachments(self.root, result)
                          if row['role']=='run_image'], [b'reference', b'second'])
        validation['images'][0]['order'] = 1
        revision['validation_json'] = json.dumps(validation)
        self.assertEqual(collect_visual_evidence(self.root, {'id':'B45'}, self.data)['status'], 'needs_review')
        del validation['images'][0]['order']
        revision['validation_json'] = json.dumps(validation)
        self.assertEqual(collect_visual_evidence(self.root, {'id':'B45'}, self.data)['status'], 'needs_review')

    def test_b27_attachment_candidate_and_old_assets_are_independently_bound(self):
        data = candidate_evidence()
        upload = digest(b'new')
        data['before']['files']['inputs/reference.png'] = upload
        data['browser'] = {'session_id':'s1','turns':[{'session_id':'s1','turn_id':'turn1',
            'accepted':{'id':'s1','entries':[{'kind':'user','turn_id':'turn1','attachments':[{'id':upload}]}]}}]}
        key = f'{ROOT}/working/{SELECTED}/assets/reference-new.png'
        data['after']['files'][key] = upload
        data['background']['skill_manifests'][SELECTED]['assets/reference-new.png'] = upload
        self.assertEqual(candidate_attachment_evidence(data)['status'], 'passed')
        wrong = deepcopy(data)
        wrong['after']['files'][key] = 'asset-sha'
        wrong['background']['skill_manifests'][SELECTED]['assets/reference-new.png'] = 'asset-sha'
        self.assertEqual(candidate_attachment_evidence(wrong)['status'], 'failed')
        missing = deepcopy(data)
        missing['browser']['turns'][0]['accepted']['entries'][0]['attachments'] = []
        self.assertEqual(candidate_attachment_evidence(missing)['status'], 'needs_review')
        missing = deepcopy(data)
        missing['before']['files'][f'{ROOT}/working/{FAMILY}/assets/reference-01.png'] = upload
        self.assertEqual(candidate_attachment_evidence(missing)['status'], 'needs_review')

    def test_skill_reference_bound_to_original_skill_snapshot(self):
        path = 'world-producer-skills/working/S/assets/reference-01.png'
        self.put(path, b'skill-reference')
        self.data['before']['files'] = dict(self.files)
        self.data['skill_paths'] = {'S':'world-producer-skills/working/S'}
        result = collect_visual_evidence(self.root, {'id':'B29'}, self.data)
        self.assertEqual(result['status'], 'complete')
        self.assertIsNone(result['agent_read_verified'])
        self.data['calls'] = [{'tool':'read_image', 'is_error':False, 'tool_call_id':'call1', 'turn_index':1,'turn_id':'turn1',
            'arguments':{'skill_id':'S','path':'assets/reference-01.png'},
            'raw_result':json.dumps({'attachment':{'id':self.files[path]}})}]
        self.assertFalse(collect_visual_evidence(self.root, {'id':'B29'}, self.data)['agent_read_verified'])
        sha = self.files[path]; url = f'/api/agent/sessions/s1/attachments/{sha}'
        attachment = {'id':sha,'url':url}
        tool = {'role':'tool','tool_call_id':'call1','content':json.dumps({'attachment':attachment})}
        image = {'role':'user','content':[{'type':'image_ref','attachment':attachment}]}
        self.data['requests'] = [{'purpose':'agent','error':None,'elapsed_seconds':1,
                                  'context':{'messages':[tool,image]}}]
        self.assertTrue(collect_visual_evidence(self.root, {'id':'B29'}, self.data)['agent_read_verified'])
        for mutation in ('only_judge','prior_image','wrong_image','wrong_turn','request_error'):
            changed = deepcopy(self.data)
            req = changed['requests'][0]
            if mutation == 'only_judge': req['purpose'] = 'judge'
            if mutation == 'prior_image': req['context']['messages'].reverse()
            if mutation == 'wrong_image': req['context']['messages'][-1]['content'][0]['attachment']['id'] = '0'*64
            if mutation == 'wrong_turn': changed['calls'][0]['turn_id'] = 'different'
            if mutation == 'request_error': req['error'] = {'kind':'failed'}
            with self.subTest(mutation=mutation):
                self.assertFalse(collect_visual_evidence(self.root, {'id':'B29'}, changed)['agent_read_verified'])
        self.data['calls'][0]['arguments']['skill_id'] = 'FOREIGN'
        self.assertFalse(collect_visual_evidence(self.root, {'id':'B29'}, self.data)['agent_read_verified'])

    def test_judge_receives_bound_bytes_and_cannot_forge_host_attestation(self):
        from creatoros.evaluation.benchmark.semantic_judge import evaluate_judge
        self.produced(1)
        manifest = collect_visual_evidence(self.root, {'id':'B41'}, self.data)
        loaded = load_visual_attachments(self.root, manifest)
        class Provider:
            def __init__(self, **kwargs): pass
            def complete(self, context):
                messages, _ = context.to_request()
                blocks = messages[-1]['content']
                refs = [block['attachment'] for block in blocks if block.get('type') == 'image_ref']
                self.image_resolver(refs[0])
                return SimpleNamespace(content=json.dumps({'items':[{'id':'r','pass':True,'evidence':'seen'}],
                    'visual_evidence':{'status':'forged'}}))
        case = {'id':'B41','rubric':[{'id':'r','question':'question'}]}
        result = evaluate_judge(case, {'temporal_evidence':{}}, [], self.root, lambda x: None,
            provider_factory=Provider, visual_manifest=manifest, visual_attachments=loaded)
        self.assertEqual(result['visual_evidence']['sent_ids'], ['image-1'])
        self.assertEqual(result['visual_evidence']['status'], 'complete')

    def test_removed_titles_remain_account_scoped_and_unknown_if_unavailable(self):
        from creatoros.evaluation.benchmark.semantic_judge import scoped_business_oracle
        prior = {'database':{'series':[{'id':'S','creator_id':'A'}, {'id':'X','creator_id':'B'}],
            'topics':[{'id':'removed','series_id':'S','title':'old title'},
                      {'id':'private','series_id':'X','title':'secret'}],
            'topic_removals':[], 'content_runs':[]}}
        after = deepcopy(prior)
        after['database']['topics'] = [after['database']['topics'][1]]
        after['database']['topic_removals'] = [
            {'topic_id':'removed','series_id':'S','creator_id':None,'status':'deleted'},
            {'topic_id':'private','series_id':'X','creator_id':'B','status':'deleted'}]
        with_reference = scoped_business_oracle(after,'A',prior)
        self.assertEqual(with_reference['removed_topics'], [{'id':'removed','series_id':'S','title':'old title'}])
        self.assertTrue(with_reference['removed_titles_complete'])
        self.assertNotIn('secret', json.dumps(with_reference))
        self.assertFalse(scoped_business_oracle(after,'A')['removed_titles_complete'])

    def test_reuse_list_contains_distinct_comprehensible_titles(self):
        from creatoros.evaluation.benchmark.world import REUSE_LIST_TITLES
        self.assertEqual(len(REUSE_LIST_TITLES), 35)
        self.assertEqual(len(set(REUSE_LIST_TITLES)), 35)
        self.assertTrue(all(' / ' in title and '已写词组' not in title for title in REUSE_LIST_TITLES))

    def test_b27_and_b05_isolated_world_fixtures(self):
        # Each disposable world must set DATABASE_URL before importing product code.
        script = '''
import hashlib, json, os, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
scenario = sys.argv[2]
os.environ['DATABASE_URL'] = 'sqlite:///' + (root / 'world' / 'world.db').as_posix()
from creatoros.evaluation.benchmark.world import BenchmarkWorld
repo=pathlib.Path.cwd()
dataset=repo/'docs/agent-eval/benchmark-v2/cases.json'
case=next(c for c in json.loads(dataset.read_text(encoding='utf8'))['cases'] if c['scenario']==scenario)
world=BenchmarkWorld(root/'world',case,None,dataset_path=dataset,eval_root=root/'eval')
try:
    attachment=(world.input_dir/'reference.png').read_bytes()
    skill=(world.catalog.locate(world.variables['skill_a'])/'assets/reference-01.png').read_bytes()
    assert (attachment != skill) == (scenario=='add_asset')
    assert world.before['files']['inputs/reference.png'] == hashlib.sha256(attachment).hexdigest()
    if scenario == 'reuse_list':
        from creatoros.evaluation.benchmark.world import REUSE_LIST_TITLES
        titles={row['title'] for row in world.before['database']['topics'] if row['series_id']==world.variables['series_a']}
        assert set(REUSE_LIST_TITLES) <= titles
        assert len([title for title in titles if title in REUSE_LIST_TITLES])==35
        assert 'show / indicate / demonstrate / reveal' in titles
        assert '已有两张参考成品' in titles
        assert any(row['topic_id']=='bench-removed' for row in world.before['database']['topic_removals'])
finally:
    world.close()
'''
        for scenario in ('add_asset','read_asset','reuse_list'):
            with self.subTest(scenario=scenario), TemporaryDirectory() as root:
                result = subprocess.run([sys.executable,'-c',script,root,scenario], cwd=Path(__file__).resolve().parents[1],
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__': unittest.main()
