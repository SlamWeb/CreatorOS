"""Conservative qualification when a verdict needs unbound visual evidence.

The current collector does not yet record verified image-to-role bindings. This
module cannot certify any of these visual verdicts, even if a model says it saw
an image. Both positive and negative verdicts are qualified; raw judge JSON is never changed.
"""
from copy import deepcopy

VERSION = 'visual-evidence-qualification-v1'
DEPENDENT_RULES = {
    'B04': {'b04-s1'}, 'B29': {'b29-s1', 'b29-s2'}, 'B41': {'b41-s1', 'b41-s2'},
    'B42': {'b42-s1'}, 'B44': {'b44-s1'}, 'B45': {'b45-s1', 'b45-s2'},
    'B47': {'b47-s1'}, 'B49': {'b49-s3'},
}


def qualify_visual_judgments(case, judge):
    result = deepcopy(judge)
    affected = DEPENDENT_RULES.get(case.get('id'), set())
    for item in result.get('items', []):
        if item.get('id') in affected and item.get('pass') is not None:
            item['pass'] = None
            item['qualification'] = VERSION
            item['evidence'] += '；所需媒体与任务身份的绑定或媒体内容证据尚未由采证器核验，该结论待复核。'
    return result
