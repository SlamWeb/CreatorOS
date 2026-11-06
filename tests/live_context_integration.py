"""Opt-in real DeepSeek + isolated Studio read-only context integration.

Run ``python -m tests.live_context_integration --live`` for the paid probe.
All accounts, data and SQLite are temporary; tool dispatch is restricted to
two read-only manage_topics actions. No historical tasks are executed.
"""
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from unittest.mock import patch
from uuid import uuid4

from dotenv import dotenv_values
import httpx
from sqlalchemy import func, select

from creatoros.agent import loop as loop_module
from creatoros.ai import DeepSeekProvider
from creatoros.ai.types import ToolCall
from creatoros.session.context_trace import read_trace
from creatoros.storage import ContentRepository, CreatorPlatform, TopicSource
from creatoros.storage.models import ContentRun
from creatoros.tools.execution import execute_tool_call
from creatoros.web import chat as chat_module
from creatoros.web.app import create_app
from creatoros.web.chat import ChatRuntimeContext
from tests.agent_studio_support import serve
from tests.studio_review_fixtures import make_fixture


def send(client, doc, text):
    turn = str(uuid4())
    response = client.post(f"/api/agent/sessions/{doc['id']}/turns", json={
        "request_id": turn, "expected_version": doc['version'], "text": text,
        "reasoning_effort": "low"})
    response.raise_for_status()
    deadline = monotonic() + 180
    while monotonic() < deadline:
        doc = client.get(f"/api/agent/sessions/{doc['id']}").json()
        if doc['status'] != 'running':
            if doc['status'] != 'idle':
                raise RuntimeError('isolated agent turn did not finish cleanly')
            return turn, doc
        sleep(.2)
    raise TimeoutError('isolated agent turn timed out')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='make paid DeepSeek requests')
    parser.add_argument('--env-file', type=Path, default=Path.cwd() / '.env')
    args = parser.parse_args()
    if not args.live:
        print('live_context_integration=not_run pass --live to call the model')
        return
    key = (dotenv_values(args.env_file).get('DEEPSEEK_API_KEY') or '').strip()
    if not key:
        raise RuntimeError('DEEPSEEK_API_KEY is unavailable')
    marker = 'CHECK' + uuid4().hex[:12].upper()
    calls = []

    def guarded(call, context=None, *, model_requested=False):
        data = json.loads(call.arguments or '{}')
        if (call.name != 'manage_topics' or data.get('action') not in {'get', 'list'}
                or context is None or context.creator_id != 'review-lab'):
            raise RuntimeError('blocked non-read-only or out-of-scope dispatch')
        result = execute_tool_call(call, context=context, model_requested=model_requested)
        calls.append({'action': data['action'], 'error': result.is_error})
        return result

    def provider():
        return DeepSeekProvider(key, model='deepseek-flash', timeout_seconds=90, max_retries=0)

    with TemporaryDirectory(prefix='creatoros-context-integration-') as temporary:
        root = Path(temporary)
        database, run_service, producer, _ = make_fixture(root)
        repository = ContentRepository(database)
        # Keep the marker beyond the list preview so the detail query actually
        # needs a get call; otherwise the earlier list may already reveal it.
        repository.add_topic(topic_id='review-check', series_id='agent-notes', title='隔离测试目标',
                             source=TopicSource.MANUAL, brief='合成制作要求。' * 40 + '测试专用校验码：' + marker)
        repository.create_creator(creator_id='other-owner', display_name='隔离测试另一账号',
                                  platform=CreatorPlatform.XIAOHONGSHU)
        repository.create_series(series_id='other-series', creator_id='other-owner', name='另一栏目',
                                 description='', audience='', skill_name='')
        repository.add_topic(topic_id='other-topic', series_id='other-series', title='不能跨账号读取',
                             source=TopicSource.MANUAL, brief='other-secret')
        before = [(x.id, x.title, x.brief, x.status.value, x.position)
                  for x in repository.list_topics('agent-notes')]
        try:
            with patch.object(chat_module, 'ACCOUNT_TOOLS', frozenset({'manage_topics'})), \
                    patch.object(loop_module, 'execute_tool_call', side_effect=guarded):
                app = create_app(database=database, run_service=run_service,
                                 chat_root=root / 'chats', chat_provider_factory=provider)
                with serve(app) as url, httpx.Client(base_url=url, timeout=180, trust_env=False) as client:
                    response = client.post('/api/agent/sessions', json={'creator_id': 'review-lab'})
                    response.raise_for_status()
                    doc = response.json()
                    first, doc = send(client, doc, '请实际调用 manage_topics 的 list 操作，列出当前栏目“把 Agent 讲明白”的全部已入队选题标题。只读，不要修改或生产。')
                    first_text = '\n'.join(e.get('text', '') for e in doc['entries']
                                           if e.get('kind') == 'assistant' and e.get('turn_id') == first)
                    if '隔离测试目标' not in first_text or '从失败里恢复一次生产' not in first_text:
                        raise AssertionError('first turn omitted synthetic topic titles')
                    second, doc = send(client, doc, '请实际调用 manage_topics 的 get 操作，读取选题 review-check 的完整制作要求，告诉我其中的校验码。只读，不要修改或生产。')
                    text = '\n'.join(e.get('text', '') for e in doc['entries']
                                     if e.get('kind') == 'assistant' and e.get('turn_id') == second)
                    if marker not in text:
                        raise AssertionError('second turn failed to retrieve the synthetic marker')
                    scope = ChatRuntimeContext(project_root=root, studio_url=url, creator_id='review-lab',
                                               agent_session_id=doc['id'], allowed_tools=frozenset({'manage_topics'}),
                                               archive_only_reads=True)
                    denied = execute_tool_call(ToolCall(id='scope-probe', name='manage_topics',
                        arguments=json.dumps({'action': 'get', 'topic_id': 'other-topic'})),
                        context=scope, model_requested=True)
                    if not denied.is_error or 'other-secret' in denied.content:
                        raise AssertionError('cross-account read was not denied')
            if {item['action'] for item in calls} != {'list', 'get'} or any(item['error'] for item in calls):
                raise AssertionError('required real read-only tool calls did not complete: '
                                     + json.dumps(calls, ensure_ascii=False))
            if producer.calls:
                raise AssertionError('fixture producer was invoked')
            after = [(x.id, x.title, x.brief, x.status.value, x.position)
                     for x in repository.list_topics('agent-notes')]
            with database.session() as session:
                run_count = session.scalar(select(func.count()).select_from(ContentRun))
            if before != after or run_count != 0:
                raise AssertionError('business state changed')
            trace_file = root / 'chats' / doc['id'] / 'messages.json'
            rows = read_trace(trace_file, limit=100)['items']
            finished = [r for r in rows if r.get('event') == 'finished' and r.get('request_kind') == 'main']
            following = [r for r in finished if r.get('turn_id') in {first, second}
                         and r.get('prefix_comparison', {}).get('same_turn')]
            if not following or not all(r['prefix_comparison'].get('messages_relation') == 'appended'
                                        for r in following):
                raise AssertionError('same-turn focus prefix was not preserved')
            usages = [r.get('usage') for r in finished if r.get('turn_id') in {first, second}]
            if any(not isinstance(usage, dict) for usage in usages):
                raise AssertionError('model usage missing')
            report = {'status': 'passed', 'model': 'deepseek-flash', 'turns': 2,
                      'main_requests': len(usages), 'tool_actions': calls,
                      'same_turn_appended_comparisons': len(following),
                      'list_two_titles_verified': True, 'marker_found': True, 'cross_account_denied': True,
                      'business_rows_unchanged': True, 'producer_calls': producer.calls,
                      'input_tokens': sum(u['input_tokens'] for u in usages),
                      'output_tokens': sum(u['output_tokens'] for u in usages),
                      'cache_hit_tokens': sum(u.get('cache_hit_tokens') or 0 for u in usages),
                      'cache_miss_tokens': sum(u.get('cache_miss_tokens') or 0 for u in usages),
                      'usage_per_request': usages}
            print(json.dumps(report, ensure_ascii=False))
        finally:
            database.close()


if __name__ == '__main__':
    main()
