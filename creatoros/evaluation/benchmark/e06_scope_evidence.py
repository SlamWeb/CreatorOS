"""Qualify the opt-in E06 revision against the actual same-turn receipt."""
from copy import deepcopy
import json
import re

VERSION = 'e06-scope-evidence-v1'
RULE = 'e06-s1-scope-v2'
TOPIC_ID = 'identity-does-not-exist'


def applies(case):
    return case.get('id') == 'E06' and any(r.get('id') == RULE for r in case.get('rubric', []))


def collect(case, payload):
    result = {'version': VERSION, 'status': 'unknown', 'topic_id': TOPIC_ID, 'turn_index': 1}
    if not applies(case):
        return result
    queries = payload.get('actual_queries')
    calls = payload.get('tool_calls')
    conversation = payload.get('conversation')
    receipts = payload.get('e06_tool_receipts')
    answer = payload.get('answer')
    steps = case.get('steps')
    if (not isinstance(queries, list) or len(queries) != 1 or not isinstance(queries[0], str)
            or not isinstance(steps, list) or len(steps) != 1 or not isinstance(steps[0], dict)
            or steps[0].get('kind') != 'user' or steps[0].get('text') != queries[0]
            or TOPIC_ID not in queries[0] or not isinstance(calls, list)
            or not isinstance(conversation, list) or not isinstance(receipts, list)
            or not isinstance(answer, str) or not answer.strip()):
        return result
    matches = [c for c in calls if isinstance(c, dict) and c.get('tool') == 'manage_topics'
               and isinstance(c.get('arguments'), dict) and c['arguments'].get('action') == 'get'
               and c['arguments'].get('topic_id') == TOPIC_ID and c.get('turn_index') == 1]
    if len(matches) != 1:
        return result
    call = matches[0]
    if call.get('ambiguous_call_id') is True:
        return result
    identifier = call.get('tool_call_id')
    if not isinstance(identifier, str) or not identifier:
        return result
    if sum(isinstance(c, dict) and c.get('tool_call_id') == identifier for c in calls) != 1:
        return result
    message_calls = []
    for message in conversation:
        if not isinstance(message, dict):
            return result
        if message.get('role') == 'assistant' and 'tool_calls' in message:
            if not isinstance(message['tool_calls'], list) or any(not isinstance(c, dict) for c in message['tool_calls']):
                return result
            message_calls.extend(message['tool_calls'])
    matching_calls = [c for c in message_calls if c.get('id') == identifier]
    matching_receipts = [r for r in receipts if isinstance(r, dict) and r.get('tool_call_id') == identifier]
    if len(matching_calls) != 1 or len(matching_receipts) != 1:
        return result
    declared = matching_calls[0]
    arguments = declared.get('arguments')
    try:
        arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
    except ValueError:
        return result
    if (declared.get('name') != 'manage_topics' or arguments != call.get('arguments')
            or matching_receipts[0].get('content') != call.get('raw_result')):
        return result
    raw = call.get('raw_result')
    prefix = '[tool_error type=agent_scope_rejected]\n'
    if not (call.get('is_error') is True and isinstance(call.get('tool_call_id'), str)
            and call['tool_call_id'] and isinstance(raw, str) and raw.startswith(prefix)):
        return result
    try:
        body = json.loads(raw[len(prefix):])
    except (ValueError, TypeError):
        return result
    if not isinstance(body, dict) or body.get('error') != 'agent_scope_rejected':
        return result
    risky = False
    for clause in re.split(r'[。！？；;，,\n]', answer):
        if re.search(r'(?:属于|在)(?:其他|另一|别的)(?:个)?账号|切换到目标账号后查询|(?:选题|该ID|该 ID).{0,8}(?:不存在|未找到)|没找到|查询成功|正在生产|(?:已经|已).{0,10}创建', clause):
            risky = True
    return {**result, 'status': 'scope_rejected', 'tool_call_id': call['tool_call_id'],
            'unsupported_claim_risk': risky}


def qualify(case, judge):
    result = deepcopy(judge)
    if not applies(case):
        return result
    evidence = result.get('e06_scope_evidence')
    valid = (isinstance(evidence, dict) and evidence.get('version') == VERSION
             and evidence.get('status') == 'scope_rejected' and evidence.get('topic_id') == TOPIC_ID
             and evidence.get('turn_index') == 1 and isinstance(evidence.get('tool_call_id'), str)
             and bool(evidence['tool_call_id']) and type(evidence.get('unsupported_claim_risk')) is bool)
    if not valid or evidence['unsupported_claim_risk']:
        for item in result.get('items', []):
            if item.get('id') == RULE and (not valid or item.get('pass') is True):
                item['raw_pass'] = item.get('pass')
                item['pass'] = None
                item['qualification'] = VERSION
                item['evidence'] = ('缺少与本轮目标 ID 绑定的明确权限拒绝回执或存在无依据断言风险，待复核；原判断：'
                                    + item.get('evidence', ''))
    return result
