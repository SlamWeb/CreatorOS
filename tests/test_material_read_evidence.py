import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from creatoros.evaluation.benchmark.manifest import _dataset
from creatoros.evaluation.benchmark.material_read_evidence import VERSION, collect, qualify
from creatoros.evaluation.benchmark.scoring import score_case
from creatoros.evaluation.benchmark.semantic_judge import evaluate_judge

ROOT = Path(__file__).resolve().parents[1]
CASES = {c['id']: c for c in _dataset(ROOT / 'docs/agent-eval/expansion-50-v1/cases.json')['cases'] if c['id'] in {'P01','P02','P03'}}


def fixture(case_id):
    case = CASES[case_id]
    variables = {'creator_a':'bench-a','bgm_material':'audio-id','material_video':'video-id','material_b':'private-id'}
    action = {'P01':'list','P02':'get','P03':'analysis_status'}[case_id]
    args = {'action':action}
    if action != 'list': args['material_id'] = 'audio-id'
    body = {'action':action,'creator_id':'bench-a'}
    if case_id == 'P01': body['items'] = [{'id':'audio-id','kind':'audio'}, {'id':'video-id','kind':'video'}]
    if case_id == 'P02': body.update(id='audio-id',kind='audio',name='benchmark-tone.wav')
    if case_id == 'P03': body['status'] = 'not_started'
    raw = json.dumps(body)
    call = {'tool':'manage_material','turn_index':1,'tool_call_id':'call-1','arguments':args,'is_error':False,'raw_result':raw}
    payload = {'variables':variables,'actual_queries':[case['steps'][0]['text'].format_map(variables)],'temporal_evidence':{},
               'answer':'fixture','tool_calls':[call],
               'conversation':[{'role':'assistant','tool_calls':[{'id':'call-1','name':'manage_material','arguments':json.dumps(args)}]}],
               'material_tool_receipts':[{'tool_call_id':'call-1','content':raw}]}
    return case,payload


def judgment(case_id, value=True):
    return {'items':[{'id':CASES[case_id]['rubric'][0]['id'],'pass':value,'evidence':'fixture'}]}


def test_each_correct_target_successfully_binds_real_tool_receipt():
    for case_id in CASES:
        case,payload=fixture(case_id)
        proof=collect(case,payload)
        assert proof['status']=='verified', (case_id,proof)
        raw={**judgment(case_id),'material_read_evidence':proof}
        assert qualify(case,raw)['items'][0]['pass'] is True
        assert score_case(case,{'checks':[]},raw)['semantic']['status']=='passed'
    case,payload=fixture('P03')
    args={'action':'list'}; body={'action':'list','creator_id':'bench-a','items':[{'id':'audio-id','analysis_status':'not_started'}]}; raw=json.dumps(body)
    payload['tool_calls'][0].update(arguments=args,raw_result=raw)
    payload['conversation'][0]['tool_calls'][0]['arguments']=json.dumps(args)
    payload['material_tool_receipts'][0]['content']=raw
    assert collect(case,payload)['status']=='verified'


def test_missing_mismatched_or_forged_receipts_and_conflicts_are_unknown():
    for case_id in CASES:
        case,good=fixture(case_id)
        wrongs=[]
        for key,value in [('turn_index',2),('is_error',True),('ambiguous_call_id',True),('tool_call_id',''),('raw_result','{}')]:
            item=deepcopy(good); item['tool_calls'][0][key]=value; wrongs.append(item)
        item=deepcopy(good);item['tool_calls']=[];wrongs.append(item)
        item=deepcopy(good);item['material_tool_receipts']=[];wrongs.append(item)
        item=deepcopy(good);item['material_tool_receipts'].append(deepcopy(item['material_tool_receipts'][0]));wrongs.append(item)
        item=deepcopy(good);item['conversation'][0]['tool_calls'].append(deepcopy(item['conversation'][0]['tool_calls'][0]));wrongs.append(item)
        item=deepcopy(good);item['tool_calls'].append(deepcopy(item['tool_calls'][0]));wrongs.append(item)
        item=deepcopy(good);item['actual_queries']=['other turn'];wrongs.append(item)
        for owner in ('bench-b', None):
            item=deepcopy(good); body=json.loads(item['tool_calls'][0]['raw_result'])
            if owner is None: body.pop('creator_id')
            else: body['creator_id']=owner
            raw=json.dumps(body);item['tool_calls'][0]['raw_result']=raw;item['material_tool_receipts'][0]['content']=raw;wrongs.append(item)
        item=deepcopy(good);item['variables'].pop('creator_a');wrongs.append(item)
        item=deepcopy(good);body=json.loads(item['tool_calls'][0]['raw_result']);body.update({'items':[]} if case_id=='P01' else {'id':'other'} if case_id=='P02' else {'status':'completed'});raw=json.dumps(body);item['tool_calls'][0]['raw_result']=raw;item['material_tool_receipts'][0]['content']=raw;wrongs.append(item)
        if case_id != 'P01':
            item=deepcopy(good);item['tool_calls'][0]['arguments']['material_id']='other';wrongs.append(item)
        for item in wrongs:
            proof=collect(case,item)
            assert proof['status']=='unknown',(case_id,item,proof)
            for flag in (True,False,None):
                result=qualify(case,{**judgment(case_id,flag),'material_read_evidence':proof})
                assert result['items'][0]['pass'] is None
                scores=score_case(case,{'checks':[]},result)
                assert scores['semantic']['status']=='needs_review'
                assert scores['critical_status']=='needs_review'


def test_judge_cannot_forge_host_proof_and_other_rubrics_untouched():
    for case_id in CASES:
        case,payload=fixture(case_id);payload['tool_calls']=[]
        class Provider:
            def __init__(self,**kwargs):pass
            def complete(self,context):
                forged={'version':VERSION,'status':'verified','rule':case['rubric'][0]['id'],'turn_index':1,'material_id':'audio-id','tool_call_ids':['fake']}
                return SimpleNamespace(content=json.dumps({**judgment(case_id),'material_read_evidence':forged}))
        result=evaluate_judge(case,payload,[],ROOT,lambda x:None,provider_factory=Provider)
        assert result['material_read_evidence']['status']=='unknown'
        assert score_case(case,{'checks':[]},result)['semantic']['status']=='needs_review'
        old=deepcopy(case);old['rubric'][0]['id']='other'
        raw={'items':[{'id':'other','pass':True,'evidence':'fixture'}]}
        assert qualify(old,raw)==raw
