"""Local fault injection plus optional real DeepSeek/isolated HTTP trace check."""
import argparse
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx

from creatoros.agent.loop import run_agent
from creatoros.agent.compactor import compact_session
from creatoros.ai.context import ModelContext, ContextBudget
from creatoros.ai.types import ModelResponse, ModelUsage, StreamEnd, TextDelta
from creatoros.context import RuntimeContext
from creatoros.session.context_trace import ContextTrace, breakdown, read_trace, trace_path
from creatoros.session.snapshot import save_messages, load_messages
from creatoros.terminal import Console
from tests.smoke_compact_session import summary


class Provider:
    context_window = 100000
    reserve_output_tokens = 4096
    def __init__(self, invalid=False):
        self.invalid = invalid
    def complete(self, context):
        return ModelResponse('invalid SECRET' if self.invalid else summary('done'), [], ModelUsage(51, 8, 59))
    def stream(self, context):
        yield TextDelta('ok')
        yield StreamEnd('stop', ModelUsage(101, 2, 103, 64, 37))


def invoke(path, provider, text='continue', tools=frozenset()):
    prompts = iter([text, '/exit'])
    run_agent(provider, session_file=path, runtime_context=RuntimeContext(path.parent, allowed_tools=tools),
              console=Console(input_fn=lambda _: next(prompts), output=StringIO()))


def finishes(path):
    return [r for r in read_trace(path, limit=100)['items'] if r['event'] == 'finished']


def local():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        path = root / 'messages.json'
        skill = '<available_skills>metadata</available_skills>'
        context = ModelContext.from_messages([
            {'role': 'system', 'content': 'prefix\n' + skill},
            {'role': 'user', 'content': 'SUMMARY'},
            {'role': 'tool', 'content': 'abc' * 100}], [{'schema': 'example'}])
        parts = breakdown(context, kind='main', skill_text=skill, summary_text='SUMMARY')
        assert all(n >= 0 for n in parts.values())
        assert sum(parts.values()) == ContextBudget.from_context(context).estimated_input_tokens
        assert parts['tool_results'] and parts['summary'] and parts['skills']
        from tests.smoke_auto_compaction import RecordingProvider, compaction_history
        from creatoros.web.chat import STUDIO_TOOLS
        automatic = root / 'automatic.json'
        save_messages(compaction_history(), automatic)
        invoke(automatic, RecordingProvider(), tools=STUDIO_TOOLS)
        auto_rows = finishes(automatic)
        assert [r['request_kind'] for r in auto_rows] == ['compaction', 'main']
        assert auto_rows[0]['turn_id'] == auto_rows[1]['turn_id']
        assert auto_rows[0]['output_checkpoint_id'] == auto_rows[1]['checkpoint_id']
        assert auto_rows[1]['compacted'] and auto_rows[1]['usage'] is None
        save_messages([{'role': 'system', 'content': 'SECRET'}], path)
        invoke(path, Provider())
        invoke(path, Provider())  # new host invocation, same session, distinct turn IDs
        rows = finishes(path)
        assert rows[0]['session_id'] == rows[1]['session_id']
        assert rows[0]['turn_id'] != rows[1]['turn_id']
        assert rows[0]['usage']['input_tokens'] == 101
        assert 'SECRET' not in trace_path(path).read_text()
        page = read_trace(path, limit=1)
        assert page['has_more'] and page['next_cursor'] == 1
        assert read_trace(path, after=1, limit=1)['items'][0]['event'] == 'finished'
        # Rejection must keep usage, while a preflight block must have no usage.
        old = [{'role': 'system', 'content': 's'}, {'role': 'user', 'content': 'old'},
               {'role': 'assistant', 'content': 'large history ' * 1000},
               {'role': 'user', 'content': 'new'}]
        for invalid in (True, False):
            history = old if invalid else [old[0], old[1], {'role': 'assistant', 'content': 'tiny'}, old[-1]]
            try:
                compact_session(Provider(invalid), history, [], session_file=path, keep_recent_tokens=10)
                raise AssertionError('expected summary rejection')
            except ValueError:
                record = finishes(path)[-1]
                assert record['request_kind'] == 'compaction' and record['status'] == 'failed'
                assert record['usage']['input_tokens'] == 51 and not record['compacted']
                assert record['stage'] == ('summary_validation' if invalid else 'checkpoint_gain_validation')
        cp = compact_session(Provider(), old, [], session_file=path, keep_recent_tokens=10)
        assert finishes(path)[-1]['output_checkpoint_id'] and cp
        blocked_path = root / 'blocked.json'
        save_messages([{'role': 'system', 'content': 's'}], blocked_path)
        provider = Provider()
        provider.context_window, provider.reserve_output_tokens = 500, 100
        invoke(blocked_path, provider, '中' * 1000)
        assert finishes(blocked_path)[-1]['status'] == 'blocked'
        assert finishes(blocked_path)[-1]['sent'] is False
        assert finishes(blocked_path)[-1]['usage'] is None
        try:
            compact_session(provider, [old[0], {'role': 'user', 'content': '中' * 2000},
                old[2], old[-1]], [], session_file=blocked_path, keep_recent_tokens=10)
            raise AssertionError('summary preflight expected')
        except ValueError:
            assert finishes(blocked_path)[-1]['status'] == 'blocked'
            assert finishes(blocked_path)[-1]['sent'] is False
        # Emergency externalization must be measured AFTER the large result is removed.
        emergency = root / 'emergency.json'
        huge = 'x' * 12000
        save_messages([old[0], {'role': 'user', 'content': 'old'},
                       {'role': 'assistant', 'tool_calls': [{'id': 'big', 'name': 'read_file', 'arguments': '{}'}]},
                       {'role': 'tool', 'tool_call_id': 'big', 'content': huge}], emergency)
        provider = Provider(invalid=True)
        provider.context_window, provider.reserve_output_tokens = 1200, 100
        invoke(emergency, provider)
        emergency_row = finishes(emergency)[-1]
        assert emergency_row['externalized'] and emergency_row['sent']
        assert emergency_row['estimated_input_tokens'] < emergency_row['tokens_before']
        assert huge in str(load_messages(emergency))
        # Interruption/error must emit final metadata without exception content.
        class Broken(Provider):
            def stream(self, context):
                yield TextDelta('partial')
                raise RuntimeError('SECRET')
        try:
            invoke(root / 'broken.json', Broken())
        except RuntimeError:
            record = finishes(root / 'broken.json')[-1]
            assert record['status'] == 'failed' and record['usage'] is None
            assert 'SECRET' not in trace_path(root / 'broken.json').read_text()
        class Interrupted(Provider):
            def stream(self, context):
                yield TextDelta('partial')
                raise KeyboardInterrupt()
        invoke(root / 'interrupt.json', Interrupted())
        assert finishes(root / 'interrupt.json')[-1]['status'] == 'interrupted'
        class NoUsage(Provider):
            def stream(self, context):
                yield StreamEnd('stop')
        invoke(root / 'no-usage.json', NoUsage())
        assert finishes(root / 'no-usage.json')[-1]['usage'] is None
        # Interrupted JSONL tail cannot swallow the next request on restart.
        with trace_path(path).open('a') as stream:
            stream.write('{"partial":')
        cursor = read_trace(path, limit=100)['next_cursor']
        ContextTrace(path).append({'event': 'recovered'})
        assert read_trace(path, after=cursor, limit=100)['items'][-1]['event'] == 'recovered'
    print('context_trace_local=passed accounting isolation failures usage pagination restart')


def live():
    from creatoros.ai.deepseek import DeepSeekProvider
    from creatoros.storage import Database, upgrade_database
    from creatoros.web import create_app
    from tests.agent_studio_support import serve
    from tests.eval_studio_tasks import send_turn, ForbiddenAction

    root = Path('tmp') / ('context-trace-' + uuid4().hex[:10])
    root.mkdir(parents=True)
    url = f"sqlite:///{(root / 'eval.db').resolve().as_posix()}"
    upgrade_database(url)
    db = Database(url)
    app = create_app(database=db)
    for service in (app.state.executor, app.state.topic_research, app.state.skill_installs):
        service.submit = ForbiddenAction().submit
    provider = DeepSeekProvider(os.environ['DEEPSEEK_API_KEY'], max_retries=0, timeout_seconds=60)
    report = {'passed': False}
    try:
        with serve(app) as base, httpx.Client(base_url=base, timeout=20) as client:
            doc = client.post('/api/agent/sessions', json={}).json()
            path = root / 'eval-agent-sessions' / doc['id'] / 'messages.json'
            history = load_messages(path) + [
                {'role': 'user', 'content': '只允许查询，不启动生产。'},
                {'role': 'assistant', 'content': '已记录，只查询。' * 1500},
                {'role': 'user', 'content': '准备查询目录。'}]
            save_messages(history, path)
            cp = compact_session(provider, history, [], session_file=path, keep_recent_tokens=20)
            assert cp
            doc = send_turn(client, doc, '查询目前有哪些账号，只查询。')
            response = client.get(f"/api/agent/sessions/{doc['id']}/context-trace", params={'limit': 100})
            response.raise_for_status()
            rows = response.json()['items']
            ended = [r for r in rows if r['event'] == 'finished']
            main = [r for r in ended if r['request_kind'] == 'main']
            usages = [e for e in doc['entries'] if e['kind'] == 'usage']
            assert doc['status'] == 'idle' and main and len(main) == len(usages)
            for row, usage in zip(main, usages):
                assert row['usage']['input_tokens'] == usage['input_tokens']
                assert row['checkpoint_id'] == ended[0]['output_checkpoint_id']
                assert sum(row['estimated_parts'].values()) == row['estimated_input_tokens']
            assert ended[0]['usage'] == {**{'cache_hit_tokens': None, 'cache_miss_tokens': None}, **cp.usage.to_dict()}
            assert len({r['turn_id'] for r in main}) == 1
            other = client.post('/api/agent/sessions', json={}).json()
            assert client.get(f"/api/agent/sessions/{other['id']}/context-trace").json()['items'] == []
            assert client.get(f"/api/agent/sessions/{doc['id']}/context-trace?limit=101").status_code == 422
            report = {'passed': True, 'records': rows, 'synthetic_history': True}
    finally:
        (root / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        provider.client.close()
        db.close()
    print(f'context_trace_live=passed report={root / "report.json"}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    local()
    if args.live:
        live()
