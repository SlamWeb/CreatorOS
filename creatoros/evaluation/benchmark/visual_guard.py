"""Conservative qualification unless required media reached the Judge with identity."""
from copy import deepcopy
import re

VERSION = 'visual-evidence-qualification-v2'
DEPENDENT_RULES = {
    'B04': {'b04-s1'}, 'B29': {'b29-s1', 'b29-s2'}, 'B41': {'b41-s2'},
    'B42': {'b42-s1'}, 'B44': {'b44-s1'}, 'B45': {'b45-s1', 'b45-s2'},
    'B47': {'b47-s1'}, 'B49': {'b49-s3'},
}

REQUIRED_ROLES = {
    'B29': ['skill_reference'], 'B41': ['run_image'],
    'B42': ['run_image', 'run_image', 'run_image'], 'B44': ['user_attachment'],
    'B45': ['user_attachment', 'run_image', 'run_image'],
    'B49': ['run_image', 'run_image'],
}


def _bound(case, judge):
    from .visual_evidence import VERSION as BINDING_VERSION
    record = judge.get('visual_evidence')
    expected = REQUIRED_ROLES.get(case.get('id'))
    if not expected or not isinstance(record, dict) or record.get('version') != BINDING_VERSION or record.get('status') != 'complete' or record.get('case_id') != case.get('id'):
        return False
    bindings, sent = record.get('bindings'), record.get('sent_ids')
    if not isinstance(bindings, list) or not all(isinstance(row, dict) for row in bindings):
        return False
    ids = [row.get('id') for row in bindings]
    roles = [row.get('role') for row in bindings]
    return (all(isinstance(x, str) and x for x in ids) and len(set(ids)) == len(ids)
            and all(isinstance(role, str) for role in roles)
            and all(isinstance(row.get('sha256'), str) and re.fullmatch(r'[0-9a-f]{64}', row['sha256']) for row in bindings)
            and ids == sent and sorted(roles) == sorted(expected))


def qualify_visual_judgments(case, judge):
    result = deepcopy(judge)
    affected = DEPENDENT_RULES.get(case.get('id'), set())
    bound = _bound(case, judge)
    for item in result.get('items', []):
        # A matching image alone does not prove that the agent read it.
        needs_agent_read = item.get('id') in {'b29-s2', 'b45-s2'}
        visual = judge.get('visual_evidence')
        read_ok = isinstance(visual, dict) and visual.get('agent_read_verified') is True
        if item.get('id') in affected and item.get('pass') is not None and (not bound or (needs_agent_read and not read_ok)):
            item['pass'] = None
            item['qualification'] = VERSION
            item['evidence'] += '；所需媒体与任务身份的绑定或媒体内容证据尚未由采证器核验，该结论待复核。'
    return result
