"""Account-scoped shared Skill forks over the real Agent tool and loopback API."""
import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
from uuid import uuid4
from unittest.mock import patch

import httpx
from PIL import Image
from creatoros.ai.types import StreamEnd, TextDelta, ToolCall
from creatoros.context import RuntimeContext
from creatoros.storage import ContentRepository, Series
from creatoros.tools.execution import execute_tool_call
from creatoros.web.chat import ACCOUNT_TOOLS
from tests.agent_studio_support import serve
from tests.studio_review_fixtures import make_fixture


def image_bytes(color):
    output = BytesIO()
    Image.new('RGB', (16, 16), color).save(output, format='PNG')
    return output.getvalue()


class SharedSkillForkTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db, self.runs, self.producer, self.app = make_fixture(self.root)
        self.addCleanup(self.db.close)
        self.catalog = self.app.state.skill_installs.catalog
        self.repo = ContentRepository(self.db)
        self.repo.create_creator(creator_id='other', display_name='B')
        self.repo.create_creator(creator_id='third', display_name='C')
        source = self.root / 'shared-source'
        (source / 'assets').mkdir(parents=True)
        self.old_text = ('---\nname: shared-comic\ndescription: shared\n'
                         'creatoros-output: social-content-pack.image-carousel\n---\n'
                         '[参考](assets/old.png)\n')
        (source / 'SKILL.md').write_text(self.old_text, encoding='utf-8')
        self.old_text = (source / 'SKILL.md').read_bytes().decode('utf-8')
        self.old_image = image_bytes('blue')
        (source / 'assets' / 'old.png').write_bytes(self.old_image)
        self.original = self.catalog.register_local(source, role='legacy_end_to_end')
        with self.db.session() as session:
            session.get(Series, 'agent-notes').skill_name = self.original['id']
            session.add(Series(id='other-series', creator_id='other', name='B', description='', audience='',
                               skill_name=self.original['id']))
        self.a = self.app.state.chat.create('review-lab')['id']
        self.b = self.app.state.chat.create('other')['id']
        self.c = self.app.state.chat.create('third')['id']
        self.server = serve(self.app)
        self.url = self.server.__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        self.client = httpx.Client(base_url=self.url, timeout=15, trust_env=False)
        self.addCleanup(self.client.close)

    def context(self, creator, sid, turn):
        return RuntimeContext(project_root=self.root, studio_url=self.url, creator_id=creator,
                              agent_session_id=sid, user_request_id=turn, allowed_tools=ACCOUNT_TOOLS)

    def invoke(self, args, creator='review-lab', sid=None, turn='shared-edit'):
        return execute_tool_call(ToolCall('edit', 'update_producer_skill_file', json.dumps(args)),
                                 context=self.context(creator, sid or self.a, turn), model_requested=True)

    def attachment(self, sid, color='red'):
        raw = image_bytes(color)
        return raw, self.app.state.chat.attachments.put(sid, 'reference.png', base64.b64encode(raw).decode())['id']

    def headers(self, sid):
        return {'x-creatoros-agent-session': sid}

    def test_scoped_version_scan_keeps_unrelated_reads_responsive_and_serializes_writes(self):
        """A slow Skill history must not stall the server's entire event loop."""
        started, release, write_started = Event(), Event(), Event()
        history = self.app.state.skill_versions.history
        create = self.app.state.writes.create_creator

        def slow_history(skill_id):
            started.set()
            if not release.wait(20):
                raise TimeoutError('test did not release Skill history')
            return history(skill_id)

        def observed_write(payload):
            write_started.set()
            return create(payload)

        url = f"/api/producer-skills/{self.original['id']}/versions"
        with patch.object(self.app.state.skill_versions, 'history', side_effect=slow_history), \
                patch.object(self.app.state.writes, 'create_creator', side_effect=observed_write):
            with ThreadPoolExecutor(max_workers=3) as pool:
                version = pool.submit(self.client.get, url, params={'creator_id': 'other'})
                try:
                    self.assertTrue(started.wait(10), 'scoped history was not reached')
                    # This route does not use the scoped lock and should answer
                    # before the slow history is released.
                    read = pool.submit(self.client.get, '/api/health')
                    self.assertEqual(read.result(timeout=5).status_code, 200)
                    write = pool.submit(self.client.post, '/api/creators', json={'display_name': '隔离并发测试'})
                    self.assertFalse(write_started.wait(.3), 'a write bypassed the scoped lock')
                finally:
                    release.set()
                self.assertEqual(version.result(timeout=20).status_code, 200)
                self.assertEqual(write.result(timeout=20).status_code, 201)
                self.assertTrue(write_started.is_set())

    def test_scoped_builtin_version_scan_also_keeps_unrelated_reads_responsive(self):
        from creatoros.web.agent_scope import AgentScopeGuard

        builtin = self.catalog.BUILTIN_ID
        self.app.state.skill_library.add('other', builtin, 'builtin-scope-test')
        started, release = Event(), Event()
        describe = AgentScopeGuard._builtin_version

        def slow_builtin(guard):
            started.set()
            if not release.wait(20):
                raise TimeoutError('test did not release built-in Skill projection')
            return describe(guard)

        with patch.object(AgentScopeGuard, '_builtin_version', new=slow_builtin):
            with ThreadPoolExecutor(max_workers=2) as pool:
                version = pool.submit(self.client.get, f'/api/producer-skills/{builtin}/versions',
                                      params={'creator_id': 'other'})
                try:
                    self.assertTrue(started.wait(10), 'built-in Skill projection was not reached')
                    read = pool.submit(self.client.get, '/api/health')
                    self.assertEqual(read.result(timeout=5).status_code, 200)
                finally:
                    release.set()
                response = version.result(timeout=20)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['items'][0]['id'], builtin)

    def test_shared_skill_permissions_reach_account_model_without_writing(self):
        captures = []

        class RecordingProvider:
            def __init__(self):
                self.client = SimpleNamespace(close=lambda: None)

            def stream(self, context):
                captures.append(deepcopy(context.to_request()))
                yield TextDelta('受控权限说明。')
                yield StreamEnd('stop')

        self.app.state.chat.provider_factory = RecordingProvider
        before = self.catalog.list_skill_versions(self.original['id'])['items']
        response = self.client.post(f'/api/agent/sessions/{self.a}/turns', json={
            'request_id': str(uuid4()), 'expected_version': 0,
            'text': '共享发布版在当前账号有什么权限？只解释，不修改、不复制、不另存。',
        })
        self.assertEqual(response.status_code, 202, response.text)
        deadline = monotonic() + 10
        while monotonic() < deadline:
            session = self.client.get(f'/api/agent/sessions/{self.a}').json()
            if session['status'] != 'running':
                break
            sleep(.02)
        else:
            self.fail('Controlled protocol turn timed out')
        self.assertEqual(session['status'], 'idle')
        self.assertEqual(len(captures), 1)
        messages, schemas = captures[0]
        instructions = '\n'.join(message['content'] for message in messages
                                 if message['role'] == 'system' and isinstance(message['content'], str))
        self.assertIn('即使原版can_edit=false', instructions)
        self.assertIn('本账号私有候选', instructions)
        self.assertIn('不能覆盖共享原件', instructions)
        self.assertIn('其他账号的私有候选', instructions)
        self.assertIn('只问权限', instructions)
        self.assertNotIn('只编辑明确can_edit的本账号Skill版本', instructions)
        edit = next(item['function'] for item in schemas if item['function']['name'] == 'update_producer_skill_file')
        self.assertIn('即使原件can_edit=false', edit['description'])
        self.assertIn('当前账号私有候选', edit['description'])
        self.assertEqual(self.catalog.list_skill_versions(self.original['id'])['items'], before)
        self.assertEqual(self.catalog.describe(self.original['id'])['digest'], self.original['digest'])
        self.assertEqual(self.producer.calls, 0)

    def test_batch_image_then_text_forks_and_preserves_other_account(self):
        raw, attachment = self.attachment(self.a)
        changed_text = self.old_text + '\nUse [new style](assets/reference-02.png).\n'
        start = self.original['digest']
        result = self.invoke({'skill_id': self.original['id'], 'expected_digest': start, 'operation': 'batch',
            'changes': [
                {'operation': 'add_image', 'path': 'assets/reference-02.png', 'attachment_id': attachment},
                {'operation': 'write_text', 'path': 'SKILL.md', 'content': changed_text},
            ]})
        self.assertFalse(result.is_error, result.content)
        receipt = json.loads(result.content)
        self.assertEqual(receipt['status'], 'completed')
        self.assertEqual(receipt['applied_count'], 2)
        candidate = receipt['skill_id']
        self.assertNotEqual(candidate, self.original['id'])
        self.assertEqual(self.catalog.describe(candidate)['version_status'], 'candidate')
        self.assertEqual(self.catalog.read_skill_file(candidate, 'assets/reference-02.png')['content'], raw)
        self.assertEqual(self.catalog.read_skill_file(candidate, 'SKILL.md')['content'], changed_text)
        self.assertEqual(self.catalog.read_skill_file(self.original['id'], 'SKILL.md')['content'], self.old_text)
        self.assertEqual(self.catalog.read_skill_file(self.original['id'], 'assets/old.png')['content'], self.old_image)
        self.assertEqual(self.catalog.describe(self.original['id'])['digest'], start)
        with self.db.session() as session:
            self.assertEqual(session.get(Series, 'agent-notes').skill_name, self.original['id'])
            self.assertEqual(session.get(Series, 'other-series').skill_name, self.original['id'])
        endpoint = f'/api/producer-skills/{candidate}'
        for sid in (self.b, self.c):
            response = self.client.get(endpoint + '/files/text', params={'path': 'SKILL.md'}, headers=self.headers(sid))
            self.assertEqual(response.status_code, 403, response.text)
            response = self.client.put(endpoint + '/files/content', headers=self.headers(sid), json={
                'path': 'SKILL.md', 'content': changed_text + 'bad', 'expected_digest': receipt['digest']})
            self.assertEqual(response.status_code, 403, response.text)
        mine = self.client.get(endpoint + '/files/text', params={'path': 'SKILL.md'}, headers=self.headers(self.a))
        self.assertEqual(mine.status_code, 200, mine.text)
        self.assertEqual(self.producer.calls, 0)

    def test_same_edit_from_two_accounts_creates_separate_candidates(self):
        changed_text = self.old_text + '\nA shared change.\n'
        request = {'skill_id': self.original['id'], 'path': 'SKILL.md', 'content': changed_text,
                   'expected_digest': self.original['digest']}
        a = self.invoke(request, turn='same-turn')
        b = self.invoke(request, creator='other', sid=self.b, turn='same-turn')
        self.assertFalse(a.is_error, a.content)
        self.assertFalse(b.is_error, b.content)
        aid, bid = json.loads(a.content)['skill_id'], json.loads(b.content)['skill_id']
        self.assertNotEqual(aid, bid)
        replay = self.invoke(request, turn='same-turn')
        self.assertFalse(replay.is_error, replay.content)
        self.assertEqual(json.loads(replay.content)['skill_id'], aid)
        self.assertEqual(len(self.catalog.list_skill_versions(self.original['id'])['items']), 3)
        for sid, candidate in ((self.a, bid), (self.b, aid)):
            response = self.client.get(f'/api/producer-skills/{candidate}/files/text',
                                       params={'path': 'SKILL.md'}, headers=self.headers(sid))
            self.assertEqual(response.status_code, 403, response.text)
        for sid, owned, forbidden in ((self.a, aid, bid), (self.b, bid, aid)):
            response = self.client.get(f"/api/producer-skills/{self.original['id']}/versions",
                                       headers=self.headers(sid))
            self.assertEqual(response.status_code, 200, response.text)
            ids = {item['id'] for item in response.json()['items']}
            self.assertIn(owned, ids)
            self.assertNotIn(forbidden, ids)
            self.assertIn(self.original['id'], ids)

    def test_account_scoped_web_hides_and_rejects_another_accounts_candidate(self):
        result = self.invoke({'skill_id': self.original['id'], 'path': 'SKILL.md',
            'content': self.old_text + '\nA private draft.\n', 'expected_digest': self.original['digest']})
        self.assertFalse(result.is_error, result.content)
        private = json.loads(result.content)['skill_id']
        history = f"/api/producer-skills/{self.original['id']}/versions"
        scoped = self.client.get(history, params={'creator_id': 'other'})
        self.assertEqual(scoped.status_code, 200, scoped.text)
        self.assertNotIn(private, {item['id'] for item in scoped.json()['items']})
        # A query parameter cannot replace the account from an Agent session.
        spoofed = self.client.get(history, params={'creator_id': 'review-lab'}, headers=self.headers(self.b))
        self.assertEqual(spoofed.status_code, 200, spoofed.text)
        self.assertNotIn(private, {item['id'] for item in spoofed.json()['items']})
        # Header-free global Skill management remains available to the local user.
        self.assertIn(private, {item['id'] for item in self.client.get(history).json()['items']})
        endpoint = f'/api/producer-skills/{private}/files'
        denied = self.client.get(endpoint + '/text', params={'creator_id': 'other', 'path': 'SKILL.md'})
        self.assertEqual(denied.status_code, 403, denied.text)
        denied_write = self.client.put(endpoint + '/content', params={'creator_id': 'other'}, json={
            'path': 'SKILL.md', 'content': self.old_text + '\nwrong account\n',
            'expected_digest': self.catalog.describe(private)['digest']})
        self.assertEqual(denied_write.status_code, 403, denied_write.text)
        spoofed_write = self.client.put(endpoint + '/content', params={'creator_id': 'review-lab'},
            headers=self.headers(self.b), json={'path': 'SKILL.md', 'content': self.old_text + '\nspoofed\n',
                'expected_digest': self.catalog.describe(private)['digest']})
        self.assertEqual(spoofed_write.status_code, 403, spoofed_write.text)
        denied_publish = self.client.post(f'/api/producer-skills/{private}/publish-version', params={'creator_id': 'other'}, json={
            'expected_digest': self.catalog.describe(private)['digest'], 'request_id': 'scope-publish'})
        self.assertEqual(denied_publish.status_code, 403, denied_publish.text)
        denied_switch = self.client.post('/api/series/other-series/skill-version', params={'creator_id': 'other'}, json={
            'skill_id': private, 'expected_revision': 1, 'request_id': 'scope-switch'})
        self.assertEqual(denied_switch.status_code, 403, denied_switch.text)
        allowed = self.client.get(endpoint + '/text', params={'creator_id': 'review-lab', 'path': 'SKILL.md'})
        self.assertEqual(allowed.status_code, 200, allowed.text)
        self.assertIn('A private draft.', allowed.json()['content'])
        own = self.client.post(history, params={'creator_id': 'review-lab'}, json={
            'request_id': 'web-owned-candidate', 'expected_digest': self.original['digest']})
        self.assertEqual(own.status_code, 200, own.text)
        self.assertEqual(self.catalog._record(own.json()['id']).get('creator_id'), 'review-lab')
        self.assertNotIn(own.json()['id'], {item['id'] for item in self.client.get(history, params={'creator_id': 'other'}).json()['items']})
        global_candidate = self.client.post(history, json={'request_id': 'global-candidate', 'expected_digest': self.original['digest']})
        self.assertEqual(global_candidate.status_code, 200, global_candidate.text)
        self.assertIsNone(self.catalog._record(global_candidate.json()['id']).get('creator_id'))

    def test_stale_digest_and_foreign_attachment_do_not_create_candidate(self):
        raw, attachment = self.attachment(self.a)
        before = len(self.catalog.list_skill_versions(self.original['id'])['items'])
        stale = self.invoke({'skill_id': self.original['id'], 'operation': 'add_image',
                             'attachment_id': attachment, 'path': 'assets/new.png', 'expected_digest': '0' * 64})
        self.assertTrue(stale.is_error)
        self.assertEqual(stale.error_type, 'skill_digest_conflict')
        wrong_sid = self.invoke({'skill_id': self.original['id'], 'operation': 'add_image',
                                 'attachment_id': attachment, 'path': 'assets/new.png',
                                 'expected_digest': self.original['digest']}, creator='other', sid=self.b)
        self.assertTrue(wrong_sid.is_error)
        denied = self.client.post(f'/api/agent/sessions/{self.c}/image-actions/skill-asset',
            headers=self.headers(self.c), json={'skill_id': self.original['id'], 'operation': 'add_image',
                'attachment_id': attachment, 'path': 'assets/new.png',
                'expected_digest': self.original['digest'], 'request_id': 'unauthorized-skill-test'})
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertIn('Skill', denied.text)
        self.assertNotIn('图片或栏目不属于', denied.text)
        self.assertEqual(len(self.catalog.list_skill_versions(self.original['id'])['items']), before)
        self.assertEqual(self.catalog.describe(self.original['id'])['digest'], self.original['digest'])


if __name__ == '__main__':
    unittest.main()
