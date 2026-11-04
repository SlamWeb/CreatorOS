"""Build and validate the separate, opt-in natural-language benchmark suite.

The projection includes 50 rows only for compatibility with the existing host;
only NATURAL_IDS may be selected. Unchanged rows are technical compatibility
placeholders, not natural samples. This is an integrity check, not a sandbox or
protection against a user intentionally authoring a different dataset.
"""
from copy import deepcopy
import hashlib
import json

DATASET_ID = 'creatoros-natural-benchmark-v1-20261105'
PARENT_ID = 'creatoros-trace-benchmark-v3.4-20261105'
PARENT_SHA256 = '2f92a6cba88fa9a7342edab629091a8813b34d605efe36c7d04f428db14b04e5'
DEFINITION_SHA256 = '1f3322607fac1f6d8d1853ea840372de3cdce75435dcfd5e0bd96d436e0dbb32'
POLICY_VERSION = 'natural-subset-v1'

# Each text is independently specified; identity and event steps remain frozen.
REWRITES = {
    'B05': ['把「英语辨析」（{series_a}）已经入队的选题都列一下。', '根据刚才的清单，再帮我想两个不重复的新点子。'],
    'B06': ['把选题{topic_a}的切入点原文逐字发给我。'],
    'B07': ['把「英语辨析」（{series_a}）已经入队的选题全部列出来。'],
    'B10': ['「英语辨析」（{series_a}）已经入队的选题都有哪些？', '我刚在页面加了一条，帮我再查一下最新的完整清单。'],
    'B11': ['给「英语辨析」（{series_a}）加个选题：borrow与lend，切入点是同学借书的两个方向。'],
    'B12': ['把 rise与raise、accept与except、affect与effect 分别加到「英语辨析」（{series_a}），都做四格辨析选题。'],
    'B13': ['把选题{topic_a}的切入点改成「在图书馆借书，用同一场景解释借入和借出。」'],
    'B14': ['把选题{topic_a}的标题改成「借书到底用borrow还是lend」。'],
    'B15': ['把选题{topic_a}从选题里移除。'],
    'B16': ['给「英语辨析」（{series_a}）加个选题「图书馆里的borrow与lend」，切入点是同学借书。', '刚才那条加好了吗？'],
    'B17': ['把选题{topic_a}的切入点清空。'],
    'B20': ['「英语辨析」（{series_a}）还有哪些选题没开始生产？'],
    'B21': ['把「英语辨析」（{series_a}）改名为「四格英语辨析」。'],
    'B22': ['新建一个「四格辨词实验」栏目，用已安装的Skill {skill_a}。'],
    'B25': ['把Skill {skill_a}的完整制作要求发我看看。'],
    'B38': ['Skill {skill_a}对图片制作有哪些要求？'],
    'B40': ['把我的选题{topic_a}的完整切入点发给我。'],
    'B41': ['用Skill {skill_a}给选题{topic_a}做一张四格英语辨析图。'],
    'B44': ['根据这张图创建一个可复用的图片制作Skill草稿，重点是配色和留白。'],
    'B48': ['给「英语辨析」（{series_a}）找一组常见易混英语词，用权威词典核实，给我一个带来源的候选。'],
}
NATURAL_IDS = tuple(REWRITES)


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical_digest(document):
    return _digest(json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def build_document(parent_bytes):
    parent = json.loads(parent_bytes)
    if PARENT_SHA256 == 'PENDING' or canonical_digest(parent) != PARENT_SHA256:
        raise ValueError('Natural suite requires the exact frozen parent definition')
    if parent.get('dataset_id') != PARENT_ID:
        raise ValueError('Natural suite parent dataset identity differs')
    doc = deepcopy(parent)
    doc['dataset_id'] = DATASET_ID
    doc['status'] = 'natural_subset_ready_for_new_freeze'
    doc['natural_selection_policy'] = {'version': POLICY_VERSION, 'parent_dataset_id': PARENT_ID,
        'parent_canonical_sha256': PARENT_SHA256, 'digest_algorithm': 'sha256-json-sortkeys-utf8-v1',
        'eligible_case_ids': list(NATURAL_IDS),
        'unlisted_cases': 'compatibility placeholders; must not be selected'}
    doc['natural_provenance'] = ('These user requests are synthetic rewrites grounded in parent task intent; '
        'not real user quotations. Other 30 cases are unchanged compatibility placeholders.')
    cases = {case['id']: case for case in doc['cases']}
    for identifier, messages in REWRITES.items():
        case = cases[identifier]
        steps = [step for step in case['steps'] if step['kind'] == 'user']
        if len(steps) != len(messages):
            raise ValueError(f'{identifier} parent user turn structure changed')
        for step, message in zip(steps, messages):
            step['text'] = message
        case['origin'] = deepcopy(case['origin'])
        case['origin']['type'] = 'adversarial_variant'
        case['origin']['adaptation'] = ('Synthetic natural-language rewrite of parent task; '
            'not a verbatim user request. Parent evidence is retained as provenance only.')
    # User no longer forbids a second list request; do not grade an unrequested
    # efficiency constraint. Still evaluate factual nonduplicate suggestions and
    # the implicit boundary that suggestions are not authorization to save.
    task = cases['B05']
    task['intent'] = '先读取目录，再基于已有选题提出两个新点子。'
    task['expected_outcome'] = '首轮列全有效已入队目录；第二轮给出两个彼此及与已有目录不实质重复的建议，不擅自入队。'
    task['trajectory']['required'] = task['trajectory']['required'][:1]
    task['scoring']['program_checks'] = [r for r in task['scoring']['program_checks'] if r['id'] not in {'required_01', 'required_02'}]
    next(r for r in task['rubric'] if r['id'] == 'b05-s3')['question'] = '第二轮是否给出建议且没有声称已保存或生产？允许为确认目录而再次只读查询。'
    # Listing all results is the contract, not a particular tool route.
    task = cases['B07']
    task['scoring']['program_checks'] = [r for r in task['scoring']['program_checks'] if r['id'] != 'required_01']
    # The second turn requests a fresh lookup, but not a specific tool API.
    task = cases['B10']
    task['scoring']['program_checks'] = [r for r in task['scoring']['program_checks'] if r['id'] not in {'required_01', 'required_02'}]
    # "Make one picture" specifies the delivered result, not the number of
    # internal generation items. Keep outcome_05 available as diagnostics.
    task = cases['B41']
    task['scoring']['program_checks'] = [r for r in task['scoring']['program_checks'] if r['id'] != 'outcome_05']
    return doc


def is_natural_document(doc):
    return (str(doc.get('dataset_id', '')).startswith('creatoros-natural-')
            or 'natural_selection_policy' in doc or 'natural_provenance' in doc)


def validate_natural_document(doc):
    if not is_natural_document(doc):
        return False
    policy = doc.get('natural_selection_policy')
    expected = {'version': POLICY_VERSION, 'parent_dataset_id': PARENT_ID,
                'parent_canonical_sha256': PARENT_SHA256, 'digest_algorithm': 'sha256-json-sortkeys-utf8-v1',
                'eligible_case_ids': list(NATURAL_IDS),
                'unlisted_cases': 'compatibility placeholders; must not be selected'}
    if doc.get('dataset_id') != DATASET_ID or policy != expected:
        raise ValueError('Natural suite identity or eligible-case policy is invalid')
    if DEFINITION_SHA256 == 'PENDING' or canonical_digest(doc) != DEFINITION_SHA256:
        raise ValueError('Natural suite frozen definition differs from registered version')
    return True


def validate_selection(doc, case_ids):
    if not validate_natural_document(doc):
        return
    if case_ids is None or not case_ids:
        raise ValueError('Natural suite requires an explicit eligible-case selection')
    if any(identifier not in NATURAL_IDS for identifier in case_ids):
        raise ValueError('Natural suite selection includes a non-natural compatibility placeholder')
