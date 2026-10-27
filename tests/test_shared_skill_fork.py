"""Account-scoped shared Skill forks over the real Agent tool and loopback API."""
import base64
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import httpx
from PIL import Image
from creatoros.ai.types import ToolCall
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
