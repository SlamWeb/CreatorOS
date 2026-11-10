"""Read-only skill regression admission on an isolated real local server.

No paid model, production, or user data is involved. The test is run in a
separate Python process because DATABASE_URL and service initialization are
process scoped.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


def worker(root, case_id):
    import json
    from uuid import uuid4
    root = Path(root).resolve()
    os.environ['DATABASE_URL'] = 'sqlite:///' + (root / 'world/world.db').as_posix()
    os.environ['CREATOROS_EVAL_REGISTRY'] = str(root / 'outside-registry.sqlite')
    from creatoros.ai.types import ToolCall
    from creatoros.context import RuntimeContext
    from creatoros.evaluation.benchmark.local_dispatch_guard import DispatchGuard
    from creatoros.evaluation.benchmark.world import BenchmarkWorld
    from creatoros.tools.execution import execute_tool_call
    from creatoros.web.chat import ACCOUNT_TOOLS
    from tests.agent_studio_support import serve

    dataset = Path(__file__).resolve().parents[1] / 'docs/agent-eval/benchmark-v2/cases.json'
    case = next(c for c in json.loads(dataset.read_text(encoding='utf-8'))['cases'] if c['id'] == case_id)
    world = BenchmarkWorld(root / 'world', case, None, dataset_path=dataset, eval_root=root / 'eval',
                           defer_background_start=True)
    try:
        with serve(world.app) as base:
            session = world.app.state.chat.create(creator_id='bench-a')
            context = RuntimeContext(project_root=world.root, studio_url=base, creator_id='bench-a',
                agent_session_id=session['id'], session_file=world.root / 'sessions' / session['id'] / 'messages.json',
                allowed_tools=ACCOUNT_TOOLS, archive_only_reads=True, user_request_id=uuid4().hex)
            guard = DispatchGuard()
            execute = guard.wrap(execute_tool_call)
            def ask(name, args):
                return execute(ToolCall(uuid4().hex, name, json.dumps(args)), context, model_requested=True)

            before = world.snapshot()
            local_id = world.variables['skill_a']
            shared_id = world.variables['shared_skill']
            private_id = world.variables['skill_b']
            local = ask('get_producer_skill', {'skill_id': local_id, 'path': 'SKILL.md'})
            shared = ask('get_producer_skill', {'skill_id': shared_id, 'path': 'SKILL.md'})
            private = ask('get_producer_skill', {'skill_id': private_id, 'path': 'SKILL.md'})
            assert not local.is_error and '3:4' in local.content, (local.error_type, local.content)
            assert not shared.is_error and '3:4' in shared.content, (shared.error_type, shared.content)
            assert private.is_error and world.canary not in private.content, (private.error_type, private.content)
            listed = ask('list_producer_skills', {})
            assert not listed.is_error and world.canary not in listed.content
            for tool, args in (
                ('update_producer_skill_file', {'skill_id': shared_id, 'path': 'SKILL.md', 'content': 'change',
                                                 'expected_digest': '0' * 64}),
                ('create_skill_draft', {}),
                ('start_content_run', {}),
                ('research_series_topics', {}),
            ):
                blocked = ask(tool, args)
                assert blocked.is_error and blocked.error_type == 'local_dispatch_guard_denied', (tool, blocked)
            assert guard.score(None)['status'] == 'failed'
            after = world.snapshot()
            assert before['database'] == after['database']
            assert before['files'] == after['files']
            assert not world.app.state.background_services_started

            # Run through the actual Agent loop too: the provider first reads
            # the right version, then attempts a forbidden edit. Both results
            # must be present in the trace and the guard gate must fail closed.
            from io import StringIO
            from creatoros.ai.types import StreamEnd, TextDelta, ToolCallDelta
            import creatoros.agent.loop as agent_loop
            from creatoros.terminal import Console
            class Provider:
                context_window = 128000
                reserve_output_tokens = 4096
                def __init__(self): self.number, self.client = 0, self
                def close(self): pass
                def stream(self, _context):
                    self.number += 1
                    if self.number == 1:
                        target = local_id if case_id == 'B25' else shared_id
                        yield ToolCallDelta(0, 'read', 'get_producer_skill',
                                            json.dumps({'skill_id': target, 'path': 'SKILL.md'}))
                        yield ToolCallDelta(1, 'write', 'update_producer_skill_file',
                                            json.dumps({'skill_id': target, 'path': 'SKILL.md',
                                                        'content': 'bad', 'expected_digest': '0' * 64}))
                        yield StreamEnd('tool_calls')
                    else:
                        yield TextDelta('已停止。')
                        yield StreamEnd('stop')
            traced_guard, events = DispatchGuard(), []
            prior = agent_loop.execute_tool_call
            prompts = iter(['检查技能', '/exit'])
            try:
                agent_loop.execute_tool_call = traced_guard.wrap(prior)
                agent_loop.run_agent(Provider(), console=Console(input_fn=lambda _prompt: next(prompts), output=StringIO()),
                    session_file=context.session_file, runtime_context=context, user_request_id=uuid4().hex,
                    on_agent_event=lambda event: events.append(event))
            finally:
                agent_loop.execute_tool_call = prior
            results = {e.data['tool_call_id']: e.data for e in events if e.kind == 'tool_result'}
            assert set(results) == {'read', 'write'}, results
            assert not results['read'].get('is_error')
            assert results['write'].get('error_type') == 'local_dispatch_guard_denied'
            assert traced_guard.score([{'tool_call_id': key} for key in results])['status'] == 'failed'
            final = world.snapshot()
            assert before['database'] == final['database']
            assert before['files'] == final['files']
    finally:
        world.database.close()


class SkillReadAdmissionTests(unittest.TestCase):
    def test_local_and_shared_skill_read_without_private_leak_or_side_effects(self):
        from creatoros.integrations.media_tools import media_tool
        try:
            media_tool('ffprobe')
            media_tool('ffmpeg')
        except ValueError:
            self.skipTest('existing ffmpeg and ffprobe required')
        for case_id in ('B25', 'B30'):
            with self.subTest(case=case_id), tempfile.TemporaryDirectory() as root:
                result = subprocess.run([sys.executable, '-m', 'tests.test_regression_skill_read_admission',
                                         'worker', root, case_id], capture_output=True, text=True, timeout=90)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == 'worker':
        worker(sys.argv[2], sys.argv[3])
    else:
        unittest.main()
