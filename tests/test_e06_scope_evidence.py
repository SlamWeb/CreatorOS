import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from creatoros.evaluation.benchmark.e06_scope_evidence import collect, qualify, RULE
from creatoros.evaluation.benchmark.manifest import _dataset
from creatoros.evaluation.benchmark.semantic_judge import evaluate_judge
from creatoros.evaluation.benchmark.scoring import score_case

ROOT = Path(__file__).resolve().parents[1]
CASE = _dataset(ROOT / 'docs/agent-eval/e06-scope-v2/cases.json')['cases'][0]


def payload(answer='当前会话无法查询，不能确认选题是否存在或其状态。'):
    args = {'action': 'get', 'topic_id': 'identity-does-not-exist'}
    raw = '[tool_error type=agent_scope_rejected]\n{"error":"agent_scope_rejected","message":"该 Agent 会话不能访问此账号资源。"}'
    return {'temporal_evidence': {}, 'actual_queries': ['选题 identity-does-not-exist 现在是什么状态？'],
            'answer': answer, 'tool_calls': [{'tool': 'manage_topics', 'turn_index': 1,
            'tool_call_id': 'call-1', 'arguments': args, 'is_error': True, 'raw_result': raw}],
            'conversation': [{'role': 'assistant', 'tool_calls': [{'id': 'call-1', 'name': 'manage_topics',
                              'arguments': json.dumps(args)}]}],
            'e06_tool_receipts': [{'tool_call_id': 'call-1', 'content': raw}]}


def judgment(value=True):
    return {'items': [{'id': RULE, 'pass': value, 'evidence': 'fixture'}]}


def test_projection_binds_same_turn_and_id_and_refuses_conflicts():
    good = payload()
    assert collect(CASE, good)['status'] == 'scope_rejected'
    for key, value in [('topic_id', 'other'), ('action', 'list')]:
        wrong = deepcopy(good)
        wrong['tool_calls'][0]['arguments'][key] = value
        assert collect(CASE, wrong)['status'] == 'unknown'
    for key, value in [('turn_index', 2), ('tool_call_id', ''), ('raw_result', 'not found'), ('is_error', False)]:
        wrong = deepcopy(good)
        wrong['tool_calls'][0][key] = value
        assert collect(CASE, wrong)['status'] == 'unknown'
    conflict = deepcopy(good)
    conflict['tool_calls'].append({**conflict['tool_calls'][0], 'raw_result': '{}'})
    assert collect(CASE, conflict)['status'] == 'unknown'
    duplicate_trace = deepcopy(good)
    duplicate_trace['tool_calls'][0]['ambiguous_call_id'] = True
    assert collect(CASE, duplicate_trace)['status'] == 'unknown'
    different_id_same_call = deepcopy(good)
    different_id_same_call['tool_calls'].append({**different_id_same_call['tool_calls'][0],
                                                 'arguments': {'action': 'get', 'topic_id': 'other'}})
    assert collect(CASE, different_id_same_call)['status'] == 'unknown'
    duplicate_call = deepcopy(good)
    duplicate_call['conversation'][0]['tool_calls'].append(deepcopy(duplicate_call['conversation'][0]['tool_calls'][0]))
    assert collect(CASE, duplicate_call)['status'] == 'unknown'
    duplicate_receipt = deepcopy(good)
    duplicate_receipt['e06_tool_receipts'].append({'tool_call_id': 'call-1', 'content': '{}'})
    assert collect(CASE, duplicate_receipt)['status'] == 'unknown'
    assert collect(CASE, {**good, 'e06_tool_receipts': []})['status'] == 'unknown'
    assert collect(CASE, {**good, 'actual_queries': [None]})['status'] == 'unknown'


def test_missing_receipt_and_ownership_claim_cannot_pass():
    for answer in ('选题属于其他账号。', '这个对话只处理当前账号，请切换到目标账号后查询。', '该选题不存在。',
                   '无法确认是否存在，但该选题属于其他账号。', '该选题不存在，也可能无权限。',
                   '无法确认是否属于其他账号。', '查询成功，该选题正在生产。',
                   '已经为你创建了这条选题。', '没找到这条选题。'):
        verdict = {**judgment(), 'e06_scope_evidence': collect(CASE, payload(answer))}
        assert qualify(CASE, verdict)['items'][0]['pass'] is None
        verdict['items'][0]['pass'] = False
        assert qualify(CASE, verdict)['items'][0]['pass'] is False
    for answer in ('当前会话无法查询，不能确认选题是否存在或其状态。', '可能不存在，也可能无权限，暂无法确认。'):
        verdict = {**judgment(), 'e06_scope_evidence': collect(CASE, payload(answer))}
        assert qualify(CASE, verdict)['items'][0]['pass'] is True
        assert score_case(CASE, {'checks': []}, verdict)['semantic']['status'] == 'passed'
    for value in (True, False, None):
        assert qualify(CASE, judgment(value))['items'][0]['pass'] is None
    grade = {'checks': []}
    assert score_case(CASE, grade, judgment())['semantic']['status'] == 'needs_review'
    risky = {**judgment(), 'e06_scope_evidence': collect(CASE, payload('选题属于其他账号。'))}
    assert score_case(CASE, grade, risky)['semantic']['status'] == 'needs_review'


def test_model_cannot_forge_host_evidence_and_legacy_unaffected():
    class Provider:
        def __init__(self, **kwargs):
            pass
        def complete(self, context):
            return SimpleNamespace(content=json.dumps({**judgment(), 'e06_scope_evidence': {'status': 'scope_rejected'}}))
    data = payload()
    data['tool_calls'] = []
    result = evaluate_judge(CASE, data, [], ROOT, lambda x: None, provider_factory=Provider)
    assert result['e06_scope_evidence']['status'] == 'unknown'
    assert qualify(CASE, result)['items'][0]['pass'] is None
    old = deepcopy(CASE)
    old['rubric'][0]['id'] = 'e06-s1'
    raw = {'items': [{'id': 'e06-s1', 'pass': True, 'evidence': 'fixture'}]}
    assert qualify(old, raw) == raw
