"""Controlled material reads under v7; no model or paid service is used."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


CASES = ('P01', 'P02', 'P03')


def worker(parent, case_id):
    import json
    from io import StringIO
    from uuid import uuid4

    parent = Path(parent).resolve()
    os.environ['DATABASE_URL'] = 'sqlite:///' + (parent / 'world/world.db').as_posix()
    os.environ['CREATOROS_EVAL_REGISTRY'] = str(parent / 'registry.sqlite')

    from creatoros.ai.types import StreamEnd, TextDelta, ToolCall, ToolCallDelta
    from creatoros.context import RuntimeContext
    from creatoros.evaluation.benchmark.local_dispatch_guard import DispatchGuard
    from creatoros.evaluation.benchmark.world import BenchmarkWorld
    from creatoros.tools.execution import execute_tool_call
    from creatoros.web.chat import ACCOUNT_TOOLS
    from creatoros.terminal import Console
    from tests.agent_studio_support import serve
    import creatoros.agent.loop as agent_loop

    dataset = Path(__file__).resolve().parents[1] / 'docs/agent-eval/expansion-50-v1/cases.json'
    case = next(row for row in json.loads(dataset.read_text(encoding='utf-8'))['cases'] if row['id'] == case_id)
    world = BenchmarkWorld(parent / 'world', case, None, dataset_path=dataset,
                           eval_root=parent / 'eval', defer_background_start=True)
    try:
        with serve(world.app) as url:
            session = world.app.state.chat.create(creator_id='bench-a')
            context = RuntimeContext(project_root=world.root, studio_url=url, creator_id='bench-a',
                agent_session_id=session['id'], session_file=world.root / 'sessions' / session['id'] / 'messages.json',
                allowed_tools=ACCOUNT_TOOLS, archive_only_reads=True, user_request_id=uuid4().hex)
            guard = DispatchGuard('local-dispatch-v7')
            guarded = guard.wrap(execute_tool_call)
            def call(action, *, use_guard=True, **kwargs):
                method = guarded if use_guard else execute_tool_call
                args = {'action': action, **kwargs}
                return method(ToolCall(uuid4().hex, 'manage_material', json.dumps(args)), context,
                              model_requested=True)
            before = world.snapshot()
            assert not world.app.state.background_services_started
            listing = call('list')
            assert not listing.is_error, (case_id, listing.error_type, listing.content)
            listed = json.loads(listing.content)
            local_id = world.variables['bgm_material']
            audio = [item for item in listed['items'] if item['id'] == local_id]
            assert len(audio) == 1 and audio[0]['name'] == 'benchmark-tone.wav'
            if case_id == 'P01':
                video = [item for item in listed['items'] if item['id'] == world.variables['material_video']]
                assert len(video) == 1 and video[0]['name'] == 'bench-video.mp4'
                assert audio[0]['kind'] == 'audio' and video[0]['kind'] == 'video'
            local = call('get', material_id=local_id)
            assert not local.is_error and 'benchmark-tone.wav' in local.content
            if case_id == 'P02':
                foreign = call('get', material_id=world.variables['material_b'])
                assert foreign.is_error and world.canary not in foreign.content
            if case_id == 'P03':
                status = call('analysis_status', material_id=local_id)
                assert not status.is_error, (status.error_type, status.content)
                body = json.loads(status.content)
                assert body.get('status') == 'not_started', body
                analysis = call('analysis', material_id=local_id)
                assert analysis.error_type != 'local_dispatch_guard_denied'
            # Side-effectful operations never reach the material backend.
            for action, args in (('import_link', {'source_url': 'https://example.invalid/test'}),
                                 ('analyze', {'material_id': local_id}),
                                 ('import_status', {'import_id': 'unknown'}), ('future_action', {})):
                denied = call(action, use_guard=True, **args)
                assert denied.is_error and denied.error_type == 'local_dispatch_guard_denied'
            for raw in ('[]', '{"action":"list","action":"analyze"}', '{'):
                denied = guarded(ToolCall(uuid4().hex, 'manage_material', raw), context, model_requested=True)
                assert denied.is_error and denied.error_type == 'local_dispatch_guard_denied'
            spoof = call('list', creator_id='bench-b')
            assert spoof.is_error and world.canary not in spoof.content
            # Exercise the real Agent loop with a scripted provider. This proves
            # dispatch and tracing, not that a model answered correctly.
            action, args = ('list', {}) if case_id == 'P01' else ('get', {'material_id': local_id}) if case_id == 'P02' else ('analysis_status', {'material_id': local_id})
            class Provider:
                context_window, reserve_output_tokens = 128000, 4096
                def __init__(self): self.number, self.client = 0, self
                def close(self): pass
                def stream(self, _):
                    self.number += 1
                    if self.number == 1:
                        yield ToolCallDelta(0, 'material-call', 'manage_material', json.dumps({'action': action, **args}))
                        yield StreamEnd('tool_calls')
                    else:
                        yield TextDelta('测试完成。')
                        yield StreamEnd('stop')
            traced, events, original = DispatchGuard('local-dispatch-v7'), [], agent_loop.execute_tool_call
            prompts = iter(['测试', '/exit'])
            try:
                agent_loop.execute_tool_call = traced.wrap(original)
                agent_loop.run_agent(Provider(), console=Console(input_fn=lambda _: next(prompts), output=StringIO()),
                    session_file=context.session_file, runtime_context=context, user_request_id=uuid4().hex,
                    on_agent_event=lambda event: events.append(event))
            finally:
                agent_loop.execute_tool_call = original
            results = [event.data for event in events if event.kind == 'tool_result']
            assert len(results) == 1 and not results[0].get('is_error'), results
            assert traced.score([{'tool_call_id': 'material-call'}])['status'] == 'passed'
            assert not world.app.state.materials._analysis_jobs
            after = world.snapshot()
            assert before['database'] == after['database']
            assert before['files'] == after['files']
            assert not world.app.state.background_services_started
    finally:
        world.database.close()


class MaterialReadAdmissionTests(unittest.TestCase):
    def test_separate_worlds_and_real_read_paths(self):
        from creatoros.integrations.media_tools import media_tool
        try:
            media_tool('ffprobe')
            media_tool('ffmpeg')
        except ValueError:
            self.skipTest('ffmpeg and ffprobe required')
        for case_id in CASES:
            with self.subTest(case=case_id), tempfile.TemporaryDirectory() as root:
                result = subprocess.run([sys.executable, '-m', 'tests.test_material_read_admission', root, case_id],
                                        capture_output=True, text=True, timeout=100)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    if len(sys.argv) == 3:
        worker(sys.argv[1], sys.argv[2])
    else:
        unittest.main()
