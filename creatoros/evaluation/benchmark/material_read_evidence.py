"""Host-qualified same-turn material receipts for the P01-P03 rubrics."""
from copy import deepcopy
import json

VERSION = 'material-read-evidence-v1'
RULES = {'P01': ('materials_type', 'p01-s1'), 'P02': ('materials_mixed_scope', 'p02-s1'),
         'P03': ('materials_analysis_status', 'p03-s1')}


def applies(case):
    pair = RULES.get(case.get('id'))
    return bool(pair and case.get('scenario') == pair[0]
                and any(isinstance(r, dict) and r.get('id') == pair[1] for r in case.get('rubric', [])))


def _unique_receipt(call, calls, conversation, receipts):
    ident = call.get('tool_call_id')
    if (not isinstance(ident, str) or not ident or call.get('ambiguous_call_id') is True
            or sum(isinstance(x, dict) and x.get('tool_call_id') == ident for x in calls) != 1):
        return None
    declared = []
    for msg in conversation:
        if not isinstance(msg, dict):
            return None
        if msg.get('role') == 'assistant' and 'tool_calls' in msg:
            if not isinstance(msg['tool_calls'], list) or any(not isinstance(x, dict) for x in msg['tool_calls']):
                return None
            declared.extend(msg['tool_calls'])
    matches = [x for x in declared if x.get('id') == ident]
    outputs = [x for x in receipts if isinstance(x, dict) and x.get('tool_call_id') == ident]
    if len(matches) != 1 or len(outputs) != 1 or call.get('is_error') is not False:
        return None
    args = matches[0].get('arguments')
    try:
        args = json.loads(args) if isinstance(args, str) else args
        raw = call.get('raw_result')
        body = json.loads(raw) if isinstance(raw, str) else None
    except (ValueError, TypeError):
        return None
    if (matches[0].get('name') != 'manage_material' or args != call.get('arguments')
            or outputs[0].get('content') != raw or not isinstance(body, dict)
            or body.get('action') != args.get('action')):
        return None
    return body


def collect(case, payload):
    unknown = {'version': VERSION, 'status': 'unknown'}
    if not applies(case) or not isinstance(payload, dict):
        return unknown
    variables, calls = payload.get('variables'), payload.get('tool_calls')
    conversation, receipts = payload.get('conversation'), payload.get('material_tool_receipts')
    queries, steps = payload.get('actual_queries'), case.get('steps')
    if (not isinstance(variables, dict) or not isinstance(calls, list) or not isinstance(conversation, list)
            or not isinstance(receipts, list) or not isinstance(queries, list) or len(queries) != 1
            or not isinstance(steps, list) or len(steps) != 1 or not isinstance(steps[0], dict)
            or steps[0].get('kind') != 'user' or not isinstance(steps[0].get('text'), str)):
        return unknown
    audio, video, owner = variables.get('bgm_material'), variables.get('material_video'), variables.get('creator_a')
    if (not isinstance(owner, str) or not owner or not isinstance(audio, str) or not audio
            or (case['id'] == 'P01' and (not isinstance(video, str) or not video or video == audio))):
        return unknown
    try:
        expected_query = steps[0]['text'].format_map(variables)
    except (KeyError, ValueError, TypeError):
        return unknown
    if queries[0] != expected_query:
        return unknown
    actions = {'P01': {'list'}, 'P02': {'get'}, 'P03': {'analysis_status', 'list'}}[case['id']]
    relevant = []
    for call in calls:
        if not isinstance(call, dict) or call.get('tool') != 'manage_material' or call.get('turn_index') != 1:
            continue
        args = call.get('arguments')
        if not isinstance(args, dict) or args.get('action') not in actions:
            continue
        if args['action'] != 'list' and args.get('material_id') != audio:
            continue
        body = _unique_receipt(call, calls, conversation, receipts)
        if body is None or body.get('creator_id') != owner:
            return unknown
        action = args['action']
        if action == 'list':
            items = body.get('items')
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                return unknown
            audios = [item for item in items if item.get('id') == audio]
            if len(audios) != 1:
                continue
            if case['id'] == 'P01':
                videos = [item for item in items if item.get('id') == video]
                if len(videos) != 1:
                    continue
                valid = audios[0].get('kind') == 'audio' and videos[0].get('kind') == 'video'
            else:
                valid = audios[0].get('analysis_status') == 'not_started'
        elif action == 'get':
            valid = body.get('id') == audio and body.get('kind') == 'audio'
        else:
            valid = body.get('status') == 'not_started'
        relevant.append((valid, call['tool_call_id']))
    if not relevant or not all(valid for valid, _ in relevant):
        return unknown
    return {'version': VERSION, 'status': 'verified', 'rule': RULES[case['id']][1],
            'turn_index': 1, 'material_id': audio, 'tool_call_ids': [ident for _, ident in relevant]}


def qualify(case, judge):
    result = deepcopy(judge)
    if not applies(case):
        return result
    rule = RULES[case['id']][1]
    proof = result.get('material_read_evidence')
    valid = (isinstance(proof, dict) and proof.get('version') == VERSION and proof.get('status') == 'verified'
             and proof.get('rule') == rule and proof.get('turn_index') == 1
             and isinstance(proof.get('material_id'), str) and bool(proof['material_id'])
             and isinstance(proof.get('tool_call_ids'), list) and bool(proof['tool_call_ids'])
             and all(isinstance(x, str) and x for x in proof['tool_call_ids'])
             and len(set(proof['tool_call_ids'])) == len(proof['tool_call_ids']))
    if not valid:
        for item in result.get('items', []):
            if item.get('id') == rule:
                item['raw_pass'] = item.get('pass')
                item['pass'] = None
                item['qualification'] = VERSION
                item['evidence'] = '缺少可唯一关联本轮目标素材的真实成功回执，待复核；原判断：' + item.get('evidence', '')
    return result
