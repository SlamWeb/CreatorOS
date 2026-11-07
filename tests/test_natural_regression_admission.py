"""Natural B06/B07 fixture and local dispatch evidence, without paid calls."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
NATURAL = ROOT / 'docs/agent-eval/natural-v2/cases.json'
PARENT = ROOT / 'docs/agent-eval/benchmark-v2/cases.json'


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def worker(root, case_id):
    from io import StringIO
    from uuid import uuid4
    root = Path(root).resolve()
    os.environ['DATABASE_URL'] = 'sqlite:///' + (root / 'world/world.db').as_posix()
    os.environ['CREATOROS_EVAL_REGISTRY'] = str(root / 'registry.sqlite')
    from creatoros.ai.types import ToolCall, ToolCallDelta, StreamEnd, TextDelta
    from creatoros.context import RuntimeContext
    from creatoros.evaluation.benchmark.local_dispatch_guard import DispatchGuard
    from creatoros.evaluation.benchmark.world import BenchmarkWorld
    from creatoros.tools.execution import execute_tool_call
    from creatoros.web.chat import ACCOUNT_TOOLS
    from tests.agent_studio_support import serve
    import creatoros.agent.loop as loop
    from creatoros.terminal import Console

    case = next(c for c in load(NATURAL)['cases'] if c['id'] == case_id)
    world = BenchmarkWorld(root / 'world', case, None, dataset_path=NATURAL,
                           eval_root=root / 'eval', defer_background_start=True)
    try:
        with serve(world.app) as base:
            session = world.app.state.chat.create(creator_id='bench-a')
            context = RuntimeContext(project_root=world.root, studio_url=base, creator_id='bench-a',
                agent_session_id=session['id'], session_file=world.root / 'sessions' / session['id'] / 'messages.json',
                allowed_tools=ACCOUNT_TOOLS, archive_only_reads=True, user_request_id=uuid4().hex)
            read_guard = DispatchGuard()
            execute = read_guard.wrap(execute_tool_call)
            def ask(tool, args):
                return execute(ToolCall(uuid4().hex, tool, json.dumps(args)), context, model_requested=True)
            before = world.snapshot()
            if case_id == 'B06':
                own = ask('manage_topics', {'action': 'get', 'topic_id': world.variables['topic_a']})
                assert not own.is_error, own.content
                actual = json.loads(own.content)['topic']['brief']
                assert actual == world.content.get_topic(world.variables['topic_a']).brief
                assert actual.endswith('最终约束：右下角保留一枚小蓝点。')
                requests = [('get-own', {'action': 'get', 'topic_id': world.variables['topic_a']})]
            else:
                first = ask('manage_topics', {'action': 'list', 'series_id': world.variables['series_a'],
                                              'query': {'state': 'queued', 'offset': 0, 'limit': 100}})
                second = ask('manage_topics', {'action': 'list', 'series_id': world.variables['series_a'],
                                               'query': {'state': 'queued', 'offset': 100, 'limit': 100}})
                assert not first.is_error and not second.is_error
                items = json.loads(first.content)['items'] + json.loads(second.content)['items']
                assert len(items) == 137 and len({item['id'] for item in items}) == 137
                assert all(item['id'] != 'bench-removed' for item in items)
                requests = [('page-0', {'action': 'list', 'series_id': world.variables['series_a'],
                                       'query': {'state': 'queued', 'offset': 0, 'limit': 100}}),
                            ('page-1', {'action': 'list', 'series_id': world.variables['series_a'],
                                       'query': {'state': 'queued', 'offset': 100, 'limit': 100}})]
            foreign = ask('manage_topics', {'action': 'get', 'topic_id': world.variables['topic_b']})
            assert foreign.is_error and foreign.error_type == 'agent_scope_rejected'
            assert world.canary not in foreign.content
            blocked = ask('research_series_topics', {'series_id': world.variables['series_a']})
            assert blocked.is_error and blocked.error_type == 'local_dispatch_guard_denied'

            class Provider:
                context_window = 128000
                reserve_output_tokens = 4096
                def __init__(self): self.number, self.client = 0, self
                def close(self): pass
                def stream(self, _context):
                    self.number += 1
                    if self.number == 1:
                        for i, (call_id, args) in enumerate(requests):
                            yield ToolCallDelta(i, call_id, 'manage_topics', json.dumps(args))
                        yield ToolCallDelta(len(requests), 'network', 'research_series_topics', '{}')
                        yield StreamEnd('tool_calls')
                    else:
                        yield TextDelta('受控测试完成。')
                        yield StreamEnd('stop')
            guard, events, previous = DispatchGuard(), [], loop.execute_tool_call
            prompts = iter([case['steps'][0]['text'].format(**world.variables), '/exit'])
            try:
                loop.execute_tool_call = guard.wrap(previous)
                loop.run_agent(Provider(), console=Console(input_fn=lambda _: next(prompts), output=StringIO()),
                    session_file=context.session_file, runtime_context=context, user_request_id=uuid4().hex,
                    on_agent_event=lambda event: events.append(event))
            finally:
                loop.execute_tool_call = previous
            results = {event.data['tool_call_id']: event.data for event in events if event.kind == 'tool_result'}
            assert set(results) == {key for key, _ in requests} | {'network'}
            assert all(not results[key].get('is_error') for key, _ in requests)
            assert results['network'].get('error_type') == 'local_dispatch_guard_denied'
            assert guard.score([{'tool_call_id': key} for key in results])['status'] == 'failed'
            after = world.snapshot()
            assert before['database'] == after['database']
            assert before['files'] == after['files']
            assert not world.app.state.background_services_started
    finally:
        world.database.close()


class NaturalRegressionAdmissionTests(unittest.TestCase):
    def test_frozen_natural_selection_and_scenario_identity(self):
        from creatoros.evaluation.benchmark.natural_suite import NATURAL_IDS, validate_selection
        from creatoros.evaluation.benchmark.suite_schema import validate_selection as schema_selection
        natural, parent = load(NATURAL), load(PARENT)
        schema_selection(natural, ['B06', 'B07'])
        validate_selection(natural, ['B06', 'B07'])
        by_parent = {case['id']: case for case in parent['cases']}
        for case in natural['cases']:
            if case['id'] in {'B06', 'B07'}:
                original = by_parent[case['id']]
                self.assertEqual(case['scenario'], original['scenario'])
                self.assertEqual(case['expectations'], original['expectations'])
                self.assertNotEqual(case['steps'], original['steps'])
        placeholder = next(c['id'] for c in natural['cases'] if c['id'] not in NATURAL_IDS)
        with self.assertRaises(ValueError):
            validate_selection(natural, [placeholder])
        tampered = copy.deepcopy(natural)
        next(c for c in tampered['cases'] if c['id'] == 'B06')['steps'][0]['text'] += '额外要求'
        with self.assertRaises(ValueError):
            validate_selection(tampered, ['B06'])

    def test_real_local_world_for_natural_read_cases(self):
        from creatoros.integrations.media_tools import media_tool
        try:
            media_tool('ffprobe')
            media_tool('ffmpeg')
        except ValueError:
            self.skipTest('existing ffmpeg and ffprobe required')
        for case_id in ('B06', 'B07'):
            with self.subTest(case=case_id), tempfile.TemporaryDirectory() as root:
                result = subprocess.run([sys.executable, '-m', 'tests.test_natural_regression_admission',
                                         'worker', root, case_id], capture_output=True, text=True, timeout=100)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == 'worker':
        worker(sys.argv[2], sys.argv[3])
    else:
        unittest.main()
