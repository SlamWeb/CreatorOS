"""Fixed histories obtained from isolated real API responses, not agent trials."""
from copy import deepcopy
import json

from creatoros.integrations.studio import StudioClient
from creatoros.storage.models import Series, Topic, TopicSource


TITLES = ('请求超时为何不等于执行失败', '消息重试如何避免重复扣款',
          '数据库快照怎样影响读取', '缓存失效为什么需要版本号')
SERIES_NAME = '架构小课'


def customize(db, service, batch):
    from creatoros.integrations.producer_skills import _write
    from creatoros.storage.models import Creator
    with db.session() as session:
        session.get(Creator, 'creator-eval-a').display_name = '系统手记'
        session.get(Creator, 'creator-eval-b').display_name = '后端茶馆'
        for sid in ('series-eval-a', 'series-eval-b'):
            session.get(Series, sid).name = SERIES_NAME
    batch['snapshot'] = service.snapshot(batch['series_id'])
    for index, candidate in enumerate(batch['candidates']):
        candidate.update(title=TITLES[index], angle=f'通过订单处理场景解释第{index+1}种工程边界',
                         rationale='面向后端初学者解释机制与适用条件')
    _write(service._path(batch['id']), batch)


def add_queued(db, service, batch, expected):
    with db.session() as session:
        session.add(Topic(id=expected['id'], series_id=batch['series_id'],
                          title=expected['title'], brief=expected['brief'],
                          source=TopicSource.RESEARCH, position=1))


def api_history(base, user, requests):
    """A fixture user turn with protocol-complete tool calls and actual results."""
    client = StudioClient(base)
    calls, results = [], []
    for index, (name, arguments, method, path, payload) in enumerate(requests):
        data = client.request(method, path, **({'payload': payload} if payload else {}))
        call_id = f'fixture-call-{index}'
        calls.append({'id': call_id, 'name': name,
                      'arguments': json.dumps(arguments, ensure_ascii=False)})
        results.append({'role': 'tool', 'tool_call_id': call_id,
                        'content': json.dumps(data, ensure_ascii=False)})
    client.close()
    return [{'role': 'user', 'content': user},
            {'role': 'assistant', 'content': None, 'tool_calls': calls},
            *results, {'role': 'assistant', 'content': '以上是查询和操作结果，请按账号与批次区分。'}]


def lookup(batch):
    return ('get_topic_research', {'batch_id': batch['id']}, 'GET',
            f"/api/topic-research/{batch['id']}", None)


def history_for(case_id, base, db, batch, other=None):
    if case_id == 'H04':
        return []
    requests = [lookup(batch)]
    user = f"请查看栏目 {batch['series_id']} 的批次 {batch['id']}，先展示候选。"
    if case_id == 'H02':
        requests.append(lookup(other))
        user = f"请同时展示系统手记和后端茶馆各自的{SERIES_NAME}栏目候选，尚未选择任何一个账号。"
    elif case_id == 'H03':
        user += '我原来打算选第一条；目前只看列表，先别建立预览。'
    elif case_id == 'H06':
        user += '先给第一条准备预览，第二条以后继续；始终只给预览，不入队、不生产。'
        payload = {'selections': [{'candidate_id': 'c1'}]}
        requests.append(('prepare_topic_selection', {'batch_id': batch['id'], **payload},
                         'POST', f"/api/topic-research/{batch['id']}/preview", payload))
    history = api_history(base, user, requests)
    if case_id == 'H05':
        with db.session() as session:
            session.get(Series, batch['series_id']).description = '更新后：面向资深开发者的容量规划'
    return history


def background_history():
    # Synthetic fixed conversation, explicitly not live model-generated output.
    history = []
    for index in range(5):
        text = (f'背景记录{index}：团队讨论接口命名、日志可读性和页面留白，这段材料不要求任何业务操作。\n' * 160)
        history.extend([{'role': 'user', 'content': '以下是合成背景资料，无需执行。\n' + text},
                        {'role': 'assistant', 'content': '这段背景没有新增运营任务。'}])
    history.extend([{'role': 'user', 'content': '背景资料结束。'},
                    {'role': 'assistant', 'content': '等待后续要求。'}])
    return deepcopy(history)
