"""Stable account-scoped views for canonical-v2, the new-session default.

Reviewed multi-action tools share their property declarations.  Each
small ``oneOf`` branch expresses only its action and the required business
arguments; the original registered parser remains authoritative at runtime.
Unreviewed multi-action tools retain their existing scoped schemas. Existing
sessions retain their pinned protocol.
"""
from copy import deepcopy


_DESCRIPTIONS = {
    'manage_topics': (
        '管理栏目选题：list 列表，get 读正文，create 新增或入队，update 修改，remove 移除。'
        '列表未变可复用；问最新或发生变化时重查，分页未完继续。用户要求全文时逐字呈现。'
        '写入仅按用户要求；修改前读取原选题，移除不删除已有作品。'
    ),
    'manage_plan': (
        '创建、查看或调整当前会话计划。create 提交步骤与依赖；update 按 ID 增量修改，'
        '填 steps 或 remove_step_ids，未提及的步骤保留；'
        'pause 不取消已提交任务，resume 恢复，skip/retry 仅用于允许的失败步骤。'
        '需要生产终态后继续时设置 wait_for_completion；按结果判断后续才加 request_review。'
    ),
    'start_content_run': (
        '按用户明确要求提交制作：start 按默认 Skill 制作选题，tasks 拆分同题任务，'
        'reference 按参考附件制作，fresh 从原 Run 清空上下文重做。'
        '受理不等于完成；需要完成后继续或核验时使用计划等待。'
    ),
    'manage_skill_iteration': (
        '管理 Skill 循环迭代：start 启动，get 查询，pause 暂停，resume 恢复，continue 继续。'
        '仅用户明确要求时启动或控制；暂停不取消已提交试产，恢复保留输入和预算；'
        'continue 可增加最多 3 轮、总上限 30，部分失败先恢复当前阶段而不加轮。'
        '受理不代表完成，不自动发布或切换栏目。'
    ),
    'manage_media_collection': (
        '管理素材集：list、get、create、update、remove、export。'
        '移除不删除原图；get 的 export_id 读取既有导出，不重新打包。'
    ),
    'manage_skill_library': (
        '管理账号 Skill 库及栏目池：get 读取，add 加入账号库，remove 移除账号库引用，'
        'set_pool 替换栏目池。已有且未变化的目录可复用；仍被栏目绑定的 Skill 不可移除。'
        'set_pool 提交完整列表，空列表清空；宿主保留仍有效的旧默认。共享引用不授予修改原件权限。'
    ),
}

_SHARED_DESCRIPTIONS = {
    'manage_topics': {
        'topic_id': 'get、update 或 remove 的目标选题 ID。',
    },
    'manage_plan': {
        'plan_id': 'get、update、pause、resume、skip 或 retry 的目标计划 ID。',
        'expected_version': '明确选择已读计划版本时填写；通常省略，由宿主绑定唯一可信读取。',
    },
    'start_content_run': {
        'instructions': 'tasks 的全部任务共有要求，或 reference 的完整参考制作要求。',
        'skill_ids': 'fresh 或 reference 使用的精确 Skill 版本 ID；省略使用既定默认，不修改栏目绑定。',
    },
    'manage_skill_iteration': {
        'iteration_id': 'get、pause、resume 或 continue 的目标迭代任务 ID。',
    },
    'manage_skill_library': {
        'series_id': 'get 指定时读取栏目池，省略读取账号库；set_pool 的目标栏目 ID。',
        'expected_version': 'remove 使用已读账号库版本；set_pool 使用已读目标栏目池版本，不可混用。',
    },
}


def _shape(value):
    """Remove only prose when verifying repeated reviewed field definitions."""
    if isinstance(value, dict):
        return {key: _shape(item) for key, item in value.items() if key not in {'description', 'title'}}
    if isinstance(value, list):
        return [_shape(item) for item in value]
    return value


def _family_schema(name, entries):
    properties = {'action': {'type': 'string', 'enum': [entry['action'] for entry in entries],
                             'description': '要执行的动作。'}}
    branches = []
    for entry in entries:
        parameters = entry['schema']['function']['parameters']
        for key, value in parameters.get('properties', {}).items():
            if key == 'action':
                continue
            if key in properties and _shape(properties[key]) != _shape(value):
                raise ValueError(f'canonical view has conflicting field shapes: {name}.{key}')
            if key not in properties:
                properties[key] = deepcopy(value)
        required = [key for key in parameters.get('required', []) if key != 'action']
        branch = {'properties': {'action': {'const': entry['action']}}}
        if required:
            branch['required'] = required
        branches.append(branch)
    for key, description in _SHARED_DESCRIPTIONS.get(name, {}).items():
        if key in properties:
            properties[key]['description'] = description
    return {'type': 'function', 'function': {
        'name': name, 'description': _DESCRIPTIONS[name],
        'parameters': {'type': 'object', 'properties': properties, 'required': ['action'], 'oneOf': branches},
    }}


def _material_schema(entries, source):
    actions = [entry['action'] for entry in entries]
    groups = [(['list'], []), (['get', 'analyze', 'analysis', 'analysis_status'], ['material_id']),
              (['import_link'], ['source_url']), (['import_status'], ['import_id'])]
    known_actions = {action for names, _ in groups for action in names}
    if set(actions) - known_actions:
        return deepcopy(source)
    branches = []
    for names, required in groups:
        allowed = [name for name in names if name in actions]
        if allowed:
            branch = {'properties': {'action': {'enum': allowed}}}
            if required:
                branch['required'] = required
            branches.append(branch)
    return {'type': 'function', 'function': {
        'name': 'manage_material',
        'description': ('管理账号 BGM：list/get 查询；import_link 后台导入，import_status 查回执；'
                        'analyze 后台分析，analysis_status 查进度，analysis 读结果。受理不代表完成。'),
        'parameters': {'type': 'object', 'properties': {
            'action': {'type': 'string', 'enum': actions},
            'material_id': {'type': 'string'},
            'source_url': {'type': 'string', 'description': '用户提供的音乐或视频分享原文'},
            'import_id': {'type': 'string', 'description': '原导入回执 ID'},
            'start': {'type': 'number', 'description': '分析片段起点（秒）；默认 0'},
            'duration': {'type': 'number', 'description': '分析片段时长（秒）'},
            'force': {'type': 'boolean', 'description': '仅明确要求重新分析时为 true'},
            'offset': {'type': 'integer'},
            'limit': {'type': 'integer'},
        }, 'required': ['action'], 'oneOf': branches},
    }}


def _production_task_schema(entries, source):
    actions = [entry['action'] for entry in entries]
    groups = [(['list'], []), (['stop', 'remove'], ['task_id'])]
    if set(actions) - {action for names, _ in groups for action in names}:
        return deepcopy(source)
    branches = []
    for names, required in groups:
        allowed = [name for name in names if name in actions]
        if allowed:
            branch = {'properties': {'action': {'enum': allowed}}}
            if required:
                branch['required'] = required
            branches.append(branch)
    return {'type': 'function', 'function': {
        'name': 'manage_production_task',
        'description': ('管理当前账号选题制作任务：list 查看，stop 停止单项，remove 移除终态任务并保留产物和历史。'
                        '只在用户明确要求时停止或移除；运行中停止请求不代表已退出，勿反复轮询。'),
        'parameters': {'type': 'object', 'properties': {
            'action': {'type': 'string', 'enum': actions},
            'topic_id': {'type': 'string', 'description': '真实选题 ID'},
            'task_id': {'type': 'string', 'description': 'stop/remove 的真实任务 ID；list 不传'},
        }, 'required': ['action', 'topic_id'], 'oneOf': branches},
    }}


def canonical_v2_schemas(scoped_schemas, catalog):
    """Use original registered order and only already-authorized catalog entries."""
    by_name = {}
    for entry in catalog.values():
        by_name.setdefault(entry['name'], []).append(entry)
    result = []
    for source in scoped_schemas:
        name = source['function']['name']
        entries = by_name.get(name, [])
        if name == 'manage_production_task':
            result.append(_production_task_schema(entries, source))
        elif name == 'manage_material':
            result.append(_material_schema(entries, source))
        elif name in _DESCRIPTIONS:
            result.append(_family_schema(name, entries))
        elif len(entries) == 1 and entries[0]['action'] is None:
            result.append(deepcopy(entries[0]['schema']))
        else:
            result.append(deepcopy(source))
    return result
