"""Read-only image admission probes in per-case disposable local worlds.

These exercise real tool/HTTP authorization and session-owned copies without a
model or a new dispatch policy. Passing does not establish model vision quality.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


CASES = ('B29', 'B36', 'B45')


def worker(parent, case_id):
    import hashlib
    import json
    from io import StringIO
    from dataclasses import replace
    from uuid import uuid4

    parent = Path(parent).resolve()
    os.environ['DATABASE_URL'] = 'sqlite:///' + (parent / 'bootstrap.db').as_posix()
    os.environ['CREATOROS_EVAL_REGISTRY'] = str(parent / 'registry.sqlite')

    from sqlalchemy import select, text
    from creatoros.ai.types import StreamEnd, TextDelta, ToolCall, ToolCallDelta
    from creatoros.context import RuntimeContext
    from creatoros.evaluation.benchmark.probes import run_probes
    from creatoros.evaluation.benchmark.local_dispatch_guard import DispatchGuard
    from creatoros.evaluation.benchmark.world import BenchmarkWorld
    from creatoros.storage import ContentRevision
    from creatoros.tools.execution import execute_tool_call
    from creatoros.terminal import Console
    from creatoros.web.chat import ACCOUNT_TOOLS
    from tests.agent_studio_support import serve
    import creatoros.agent.loop as agent_loop

    dataset = Path(__file__).resolve().parents[1] / 'docs/agent-eval/benchmark-v2/cases.json'
    case = next(c for c in json.loads(dataset.read_text(encoding='utf-8'))['cases'] if c['id'] == case_id)
    world = BenchmarkWorld(parent / 'world', case, None, dataset_path=dataset,
                           eval_root=parent / 'eval', defer_background_start=True)
    try:
        for root in (world.catalog.root, world.runs.output_root, world.app.state.chat.root):
            assert Path(root).resolve().is_relative_to(world.root)
        with serve(world.app) as url:
            world.app.state.benchmark_base_url = url
            sid = world.app.state.chat.create(creator_id='bench-a')['id']
            context = RuntimeContext(project_root=world.root, studio_url=url, creator_id='bench-a',
                agent_session_id=sid, session_file=world.root / 'sessions' / sid / 'messages.json',
                allowed_tools=ACCOUNT_TOOLS, archive_only_reads=True, user_request_id=uuid4().hex)
            guard = DispatchGuard('local-dispatch-v8')
            guard.bind_world(world, world.root, trusted_root=parent)
            guarded = guard.wrap(execute_tool_call)
            def call(name, **args):
                return guarded(ToolCall(uuid4().hex, name, json.dumps(args, ensure_ascii=False)), context,
                               model_requested=True)
            def read(**args):
                return call('read_image', **args)
            def assert_attachment(result, expected):
                assert not result.is_error, (case_id, result.error_type, result.content)
                attachment = json.loads(result.content)['attachment']
                assert attachment['id'] == hashlib.sha256(expected).hexdigest()
                path = world.app.state.chat.attachments.path(sid, attachment['id'])
                assert path.resolve().is_relative_to((world.root / 'sessions' / sid).resolve())
                assert path.read_bytes() == expected
                assert result.details['image_attachments'][0]['id'] == attachment['id']
            before = world.snapshot()
            foreign_before = world.foreign_snapshot(before)
            assert not world.app.state.background_services_started
            if case_id == 'B29':
                skill = world.variables['skill_a']
                script = {'skill_id': skill, 'path': 'assets/reference-01.png'}
                assert_attachment(read(skill_id=skill, path='assets/reference-01.png'), world.reference)
                denied = read(skill_id=world.variables['skill_b'], path='assets/reference-01.png')
                assert denied.is_error and world.canary not in denied.content
                for path in ('../reference.png', str((parent / 'outside.png').resolve()), 'SKILL.md', 'assets/missing.png'):
                    denied = read(skill_id=skill, path=path)
                    assert denied.is_error, (path, denied.content)
                outside = parent / 'outside.png'
                outside.write_bytes(world.reference)
                link = world.catalog.locate(skill) / 'assets' / 'escape.png'
                os.symlink(outside, link)
                try:
                    denied = read(skill_id=skill, path='assets/escape.png')
                    assert denied.is_error, 'linked image escaped the Skill root'
                finally:
                    link.unlink()
            elif case_id == 'B36':
                foreign_path = world.app.state.chat.attachments.path(world.variables['session_b'], world.variables['attachment_b'])
                foreign_meta = foreign_path.parent / 'meta.json'
                foreign_hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (foreign_path, foreign_meta)]
                script = {'attachment_id': world.variables['attachment_b']}
                probes = run_probes(world, sid, url)
                assert len(probes) == 1, probes
                probe = probes[0]
                assert probe['target_exists'] and probe['denied'] and probe['http_status'] == 403, probe
                assert probe['attachment_tool_read']['is_error']
                assert not probe['positive_control']['denied']
                assert world.canary not in json.dumps(probe, ensure_ascii=False)
                assert foreign_hashes == [hashlib.sha256(path.read_bytes()).hexdigest() for path in (foreign_path, foreign_meta)]
            else:
                run_a, run_b = world.variables['run_a'], world.variables['run_b']
                with world.database.session() as db:
                    revision_a = db.scalar(select(ContentRevision).where(ContentRevision.content_run_id == run_a))
                    revision_b = db.scalar(select(ContentRevision).where(ContentRevision.content_run_id == run_b))
                    aid, bid = revision_a.id, revision_b.id
                    original_directory = revision_a.artifact_directory
                    validation = revision_a.validation_json
                    if isinstance(validation, str):
                        validation = json.loads(validation)
                    original_image = Path(original_directory) / validation['images'][0]['path']
                current = call('get_content_run', run_id=run_a)
                script = {'run_id': run_a, 'revision_id': aid, 'image_index': 1}
                assert not current.is_error, (current.error_type, current.content)
                assert_attachment(read(run_id=run_a, revision_id=aid, image_index=1), world.reference)
                assert_attachment(read(run_id=run_a, revision_id=aid, image_index=2), world.alternate)
                for args in ({'run_id': run_a, 'revision_id': bid, 'image_index': 1},
                             {'run_id': run_b, 'revision_id': bid, 'image_index': 1},
                             {'run_id': run_a, 'revision_id': aid, 'image_index': 3}):
                    denied = read(**args)
                    assert denied.is_error and world.canary not in denied.content, args
                assert call('get_content_run', run_id=run_b).is_error
                # Neither an external path nor another Run's attempt is valid
                # for this revision, even if it contains plausible artifacts.
                with world.database.session() as db:
                    foreign_directory = db.get(ContentRevision, bid).artifact_directory
                for bad_directory in (str(parent), foreign_directory):
                    # Deliberate fixture corruption uses raw SQL so the
                    # product's updated_at remains untouched by the test.
                    with world.database.engine.begin() as db:
                        db.execute(text('UPDATE content_revisions SET artifact_directory=:path WHERE id=:id'),
                                   {'path': bad_directory, 'id': aid})
                    denied = read(run_id=run_a, revision_id=aid, image_index=1)
                    assert denied.is_error, bad_directory
                with world.database.engine.begin() as db:
                    db.execute(text('UPDATE content_revisions SET artifact_directory=:path WHERE id=:id'),
                               {'path': original_directory, 'id': aid})
                # Existing image content is frozen by receipt SHA, not merely
                # by the fact that a file exists at the recorded location.
                content = original_image.read_bytes()
                try:
                    original_image.write_bytes(world.alternate)
                    assert read(run_id=run_a, revision_id=aid, image_index=1).is_error
                finally:
                    original_image.write_bytes(content)
            # Run the actual Agent loop: a scripted provider supplies only the
            # requested tool call, then receives the tool result and image ref.
            class Provider:
                context_window, reserve_output_tokens = 128000, 4096
                def __init__(self): self.number, self.client, self.requests = 0, self, []
                def close(self): pass
                def stream(self, state):
                    self.requests.append(state.to_request()[0])
                    self.number += 1
                    if self.number == 1:
                        yield ToolCallDelta(0, 'read-image', 'read_image', json.dumps(script, ensure_ascii=False))
                        yield StreamEnd('tool_calls')
                    else:
                        yield TextDelta('测试结束。')
                        yield StreamEnd('stop')
            provider, events = Provider(), []
            script_guard = DispatchGuard('local-dispatch-v8')
            script_guard.bind_world(world, world.root, trusted_root=parent)
            original, prompts = agent_loop.execute_tool_call, iter(['测试', '/exit'])
            try:
                agent_loop.execute_tool_call = script_guard.wrap(original)
                agent_loop.run_agent(provider, console=Console(input_fn=lambda _: next(prompts), output=StringIO()),
                    session_file=context.session_file, runtime_context=context, user_request_id=uuid4().hex,
                    on_agent_event=lambda event: events.append(event))
            finally:
                agent_loop.execute_tool_call = original
            results = [event.data for event in events if event.kind == 'tool_result']
            assert len(results) == 1, results
            assert bool(results[0].get('is_error')) == (case_id == 'B36'), results
            assert script_guard.score([{'tool_call_id': 'read-image'}])['status'] == 'passed'
            if case_id != 'B36':
                assert len(provider.requests) >= 2
                assert any(part.get('type') == 'image_ref' for message in provider.requests[-1]
                           if isinstance(message.get('content'), list) for part in message['content'])
            for name in ('start_content_run', 'research_series_topics'):
                denied = call(name, action='start')
                assert denied.is_error and denied.error_type == 'local_dispatch_guard_denied'
            # A forged context with another session, endpoint or parent path
            # never reaches the wrapped product tool.
            for bad in (replace(context, creator_id='bench-b'), replace(context, studio_url='http://127.0.0.1:1'),
                        replace(context, session_file=parent / 'outside.json'), replace(context, agent_session_id=world.variables['session_b'])):
                denied = guarded(ToolCall(uuid4().hex, 'read_image', json.dumps(script)), bad, model_requested=True)
                assert denied.is_error and denied.error_type == 'local_dispatch_guard_denied'
            for obj, attribute in ((world.catalog, 'root'), (world.runs, 'output_root'),
                                   (world.app.state.chat, 'root'), (world.app.state.chat.attachments, 'root'),
                                   (world.app.state.queries.artifacts, 'output_root')):
                original_root = getattr(obj, attribute)
                try:
                    setattr(obj, attribute, parent)
                    denied = guarded(ToolCall(uuid4().hex, 'read_image', json.dumps(script)), context, model_requested=True)
                    assert denied.is_error and denied.error_type == 'local_dispatch_guard_denied', attribute
                finally:
                    setattr(obj, attribute, original_root)
            engine = world.database.engine
            original_url = engine.url
            try:
                engine.url = original_url.set(database=str(parent / 'outside.db'))
                denied = guarded(ToolCall(uuid4().hex, 'read_image', json.dumps(script)), context, model_requested=True)
                assert denied.is_error and denied.error_type == 'local_dispatch_guard_denied', 'database root'
            finally:
                engine.url = original_url
            after = world.snapshot()
            changed_tables = [key for key in before['database'] if before['database'][key] != after['database'][key]]
            changed_files = [key for key in set(before['files']) | set(after['files']) if before['files'].get(key) != after['files'].get(key)]
            assert before == after, (case_id, changed_tables, changed_files)
            assert foreign_before == world.foreign_snapshot(after)
            assert not world.app.state.background_services_started
    finally:
        world.database.close()


class ImageReadAdmissionTests(unittest.TestCase):
    def test_each_case_uses_disposable_real_http_world(self):
        from creatoros.integrations.media_tools import media_tool
        try:
            media_tool('ffprobe')
            media_tool('ffmpeg')
        except ValueError:
            self.skipTest('ffmpeg and ffprobe required')
        for case in CASES:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as root:
                result = subprocess.run([sys.executable, '-m', 'tests.test_image_read_admission', root, case],
                                        capture_output=True, text=True, timeout=120)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    if len(sys.argv) == 3:
        worker(sys.argv[1], sys.argv[2])
    else:
        unittest.main()
