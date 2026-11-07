"""Controlled material read probes, not an executable-policy admission.

Every trial owns its SQLite and filesystem. No model or paid service is used.
The current dispatch guard still denies manage_material, including safe reads;
these tests do not by themselves authorize any benchmark case.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


CASES = ('P01', 'P02', 'P03')


def worker(parent, case_id):
    import json
    from uuid import uuid4

    parent = Path(parent).resolve()
    os.environ['DATABASE_URL'] = 'sqlite:///' + (parent / 'world/world.db').as_posix()
    os.environ['CREATOROS_EVAL_REGISTRY'] = str(parent / 'registry.sqlite')

    from creatoros.ai.types import ToolCall
    from creatoros.context import RuntimeContext
    from creatoros.evaluation.benchmark.local_dispatch_guard import DispatchGuard
    from creatoros.evaluation.benchmark.world import BenchmarkWorld
    from creatoros.tools.execution import execute_tool_call
    from creatoros.web.chat import ACCOUNT_TOOLS
    from tests.agent_studio_support import serve

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
            guard = DispatchGuard('local-dispatch-v5')
            guarded = guard.wrap(execute_tool_call)
            def call(action, *, use_guard=False, **kwargs):
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
                assert 'completed' not in json.dumps(body, ensure_ascii=False).lower(), body
            # Current policy denies even reads, and the potentially side-effectful
            # actions never reach the material backend.
            assert call('list', use_guard=True).error_type == 'local_dispatch_guard_denied'
            for action, args in (('import_link', {'source_url': 'https://example.invalid/test'}),
                                 ('analyze', {'material_id': local_id})):
                denied = call(action, use_guard=True, **args)
                assert denied.is_error and denied.error_type == 'local_dispatch_guard_denied'
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
