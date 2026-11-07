"""Unreleased v2 reference choice: isolated tools and Web; no paid producer."""
import base64
from copy import deepcopy
from hashlib import sha256
from io import BytesIO, StringIO
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
from PIL import Image

from creatoros.ai.types import ToolCall
from creatoros.agent.loop import run_agent
from creatoros.context import RuntimeContext
from creatoros.session.snapshot import load_messages
from creatoros.storage import Series
from creatoros.terminal import Console
from creatoros.tools.discovery_gateway import DISCOVER, DISCOVERY_SOURCE, ToolGateway
from tests.smoke_web_agent import wait_idle
from tests.test_reconstruct_public_contract import ScriptedProvider, env
from tests import test_reconstruct_skill_iteration_start as original_start
from creatoros.integrations.studio import StudioClient


BASELINE = json.loads((Path(__file__).parent / 'fixtures/rsi_start_before_reference_mode_v2.json').read_text(encoding='utf-8'))


class ReferenceModeSchemaTests(unittest.TestCase):
    setUp = original_start.RsiStartSchemaTests.setUp

    def test_v1_unchanged_and_unreleased_v2_requires_choice(self):
        """TR-RSI-MODE-CHECK-01: old v1 unchanged, v2 explicitly chooses intent."""
        old = ToolGateway(self.context, self.schemas, model_view_version='v1')
        new = ToolGateway(self.context, self.schemas, model_view_version='v2')
        self.assertEqual(original_start.digest(old.catalog['manage_skill_iteration.start']['schema']), original_start.V1_START_HASH)
        self.assertEqual(original_start.digest(old.model_schemas()), original_start.V1_ROOT_HASH)
        self.assertEqual(new.model_schemas(), BASELINE['root'])
        p = new.catalog['manage_skill_iteration.start']['schema']['function']['parameters']
        self.assertIn('reference_mode', p['required'])
        self.assertEqual(set(p['properties']['reference_mode']['enum']),
                         {'skill_defaults', 'attachments_only', 'selected_skill_images'})

    def test_old_v2_receipt_requires_rediscovery_same_prefix(self):
        """TR-RSI-MODE-CHECK-02: persisted prior v2 discovery is not silently reinterpreted."""
        new = ToolGateway(self.context, self.schemas, model_view_version='v2')
        old_catalog = deepcopy(new.catalog)
        old_catalog['manage_skill_iteration.start']['schema'] = BASELINE['start']
        prior = ToolGateway(self.context, self.schemas, catalog=old_catalog, model_view_version='v1')
        self.assertNotEqual(prior.versions['manage_skill_iteration.start'], new.versions['manage_skill_iteration.start'])
        discovery = ToolCall('old-find', DISCOVER, json.dumps({'names': ['manage_skill_iteration.start']}))
        receipt = prior.discover(discovery, [])
        history = [{'role': 'assistant', 'tool_calls': [{'id': discovery.id, 'name': discovery.name, 'arguments': discovery.arguments}]},
                   {'role': 'tool', 'tool_call_id': discovery.id, 'creatoros_source': DISCOVERY_SOURCE, 'content': receipt.content}]
        before = deepcopy(history)
        call = ToolCall('start', 'call_discovered_tool', json.dumps({'name': 'manage_skill_iteration.start',
            'arguments': {'series_id': 's', 'skill_id': 'k', 'topic_id': 't', 'reference_mode': 'skill_defaults'}}))
        _, error = new.resolve(call, history)
        self.assertEqual(error.error_type, 'tool_not_discovered')
        new_find = ToolCall('new-find', DISCOVER, json.dumps({'names': ['manage_skill_iteration.start']}))
        new_receipt = new.discover(new_find, history)
        history.extend([{'role': 'assistant', 'tool_calls': [{'id': new_find.id, 'name': new_find.name, 'arguments': new_find.arguments}]},
                        {'role': 'tool', 'tool_call_id': new_find.id, 'creatoros_source': DISCOVERY_SOURCE, 'content': new_receipt.content}])
        canonical, error = new.resolve(call, history)
        self.assertIsNone(error)
        self.assertEqual(json.loads(canonical.arguments)['action'], 'start')
        self.assertEqual(history[:len(before)], before)


class ReferenceModeHttpTests(unittest.TestCase):
    setUp = original_start.RsiStartHttpTests.setUp
    args = original_start.RsiStartHttpTests.args
    assert_no_worker = original_start.RsiStartHttpTests.assert_no_worker

    def submit(self, argument_list, *, owner=None, sid=None):
        ctx = RuntimeContext(self.root, creator_id=owner or self.fixture['creator_id'], agent_session_id=sid or self.sid,
            session_file=self.root / (uuid4().hex + '.json'), studio_url=self.url,
            archive_only_reads=True, allowed_tools=frozenset({'manage_skill_iteration'}))
        calls = [(f'start-{i}', 'call_discovered_tool', {'name': 'manage_skill_iteration.start', 'arguments': args})
                 for i, args in enumerate(argument_list)]
        provider = ScriptedProvider([[('find', 'discover_tools', {'names': ['manage_skill_iteration.start']})], calls, None])
        inputs = iter(('请按指定目标开始这次 Skill 迭代。', '/exit'))
        sent, original = [], StudioClient.request
        def track(client, method, path, **kwargs):
            if method == 'POST' and path == '/api/skill-iterations':
                sent.append(deepcopy(kwargs.get('payload')))
            return original(client, method, path, **kwargs)
        with patch.dict('os.environ', env('2')), patch.object(StudioClient, 'request', track):
            run_agent(provider, session_file=ctx.session_file, runtime_context=ctx,
                      console=Console(input_fn=lambda _: next(inputs), output=StringIO()))
        ledger = load_messages(ctx.session_file)
        return ([next(m['content'] for m in ledger if m.get('tool_call_id') == f'start-{i}')
                 for i in range(len(argument_list))], sent)

    def image(self, sid, color):
        b = BytesIO()
        Image.new('RGB', (23, 31), color).save(b, format='PNG')
        raw = b.getvalue()
        meta = self.app.state.chat.attachments.put(sid, 'same-name.png', base64.b64encode(raw).decode())
        return meta['id'], raw

    def two_skill_images(self):
        source = self.root / ('mode-skill-' + uuid4().hex)
        (source / 'assets').mkdir(parents=True)
        (source / 'SKILL.md').write_text('---\nname: mode-reference-skill\ndescription: isolated reference modes\n---\n隔离测试。\n', encoding='utf-8')
        for name, color in [('first.png', 'blue'), ('second.png', 'yellow')]:
            Image.new('RGB', (23, 31), color).save(source / 'assets' / name)
        item = self.iterations.catalog.register_local(source, role='mind')
        with self.db.session() as db:
            db.get(Series, self.fixture['series_id']).mind_skill_id = item['id']
        self.fixture['skill_id'] = item['id']
        directory = self.iterations.catalog.locate(item['id'])
        return [(directory / 'assets' / n).read_bytes() for n in ('first.png', 'second.png')]

    def check_created(self, results, sent, expected, expected_paths):
        self.assertEqual(len(sent), 1)
        self.assertNotIn('tool_error', results[0])
        self.assertEqual(sent[0]['reference_paths'], expected_paths)
        self.assertNotIn('reference_mode', sent[0])
        row = self.iterations._row(json.loads(results[0])['id'])
        refs = row.config_json['references']
        self.assertEqual([ref['sha256'] for ref in refs], [sha256(x).hexdigest() for x in expected])
        self.assertEqual([(self.iterations._path(row.id) / 'references' / r['file']).read_bytes() for r in refs], expected)
        self.assert_no_worker()

    def test_defaults_and_selected_attachment(self):
        """TR-RSI-MODE-CHECK-03: Skill defaults can include user-selected attachment."""
        skill = self.two_skill_images()
        aid, raw = self.image(self.sid, 'red')
        results, sent = self.submit([{**self.args(), 'reference_mode': 'skill_defaults', 'attachment_ids': [aid]}])
        self.check_created(results, sent, skill + [raw], None)

    def test_selected_skill_subset_and_attachment(self):
        """TR-RSI-MODE-CHECK-04: only selected Skill paths plus selected attachments."""
        skill = self.two_skill_images()
        aid, raw = self.image(self.sid, 'red')
        results, sent = self.submit([{**self.args(), 'reference_mode': 'selected_skill_images',
                                      'reference_paths': ['assets/second.png'], 'attachment_ids': [aid]}])
        self.check_created(results, sent, [skill[1], raw], ['assets/second.png'])

    def test_only_selected_attachments_and_same_request_id(self):
        """TR-RSI-MODE-CHECK-05: selected subset, preserved order, idempotent identity."""
        self.two_skill_images()
        a, raw_a = self.image(self.sid, 'red')
        self.image(self.sid, 'green')  # available but not selected
        c, raw_c = self.image(self.sid, 'purple')
        request = {**self.args(), 'reference_mode': 'attachments_only', 'attachment_ids': [c, a]}
        results, sent = self.submit([request, request, {**request, 'attachment_ids': [a]}])
        self.assertEqual(len(sent), 3)
        self.assertEqual(len({x['request_id'] for x in sent}), 1)
        self.assertNotIn('tool_error', results[0])
        self.assertNotIn('tool_error', results[1])
        self.assertIn('tool_error', results[2])
        self.assertEqual(json.loads(results[0])['id'], json.loads(results[1])['id'])
        row = self.iterations._row(json.loads(results[0])['id'])
        self.assertEqual([x['sha256'] for x in row.config_json['references']], [sha256(raw_c).hexdigest(), sha256(raw_a).hexdigest()])
        self.assertTrue(all(x['reference_paths'] == [] for x in sent))
        self.assert_no_worker()

    def test_missing_invalid_or_contradictory_choice_is_not_dispatched(self):
        """TR-RSI-MODE-CHECK-06: no guessed intent; reject before HTTP."""
        aid, _ = self.image(self.sid, 'red')
        invalid = [{}, {'reference_mode': 'unknown'}, {'reference_mode': 'attachments_only'},
            {'reference_mode': 'attachments_only', 'attachment_ids': [aid], 'reference_paths': None},
            {'reference_mode': 'attachments_only', 'attachment_ids': [aid], 'reference_paths': ['assets/reference.png']},
            {'reference_mode': 'skill_defaults', 'reference_paths': []},
            {'reference_mode': 'selected_skill_images', 'reference_paths': []}]
        results, sent = self.submit([{**self.args(), **values} for values in invalid])
        self.assertEqual(sent, [])
        self.assertEqual(len(results), len(invalid))
        for result in results:
            self.assertIn('tool_error', result)
            self.assertTrue('reference' in result.lower() or '参考' in result, result)
        self.assertEqual(self.iterations.list(creator_id=self.fixture['creator_id'])['items'], [])
        self.assert_no_worker()

    def test_other_session_attachment_still_rejected(self):
        """TR-RSI-MODE-CHECK-07: translating mode must not loosen ownership."""
        aid, _ = self.image(self.same_owner_sid, 'red')
        result, sent = self.submit([{**self.args(), 'reference_mode': 'attachments_only', 'attachment_ids': [aid]}])
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]['reference_paths'], [])
        self.assertIn('tool_error', result[0])
        self.assertEqual(self.iterations.list(creator_id=self.fixture['creator_id'])['items'], [])
        self.assert_no_worker()

    def test_v2_cross_account_and_invalid_skill_path_rejected(self):
        """TR-RSI-MODE-CHECK-09: v2 maintains owner and Skill path boundaries."""
        cross, _ = self.submit([{**self.args(), 'reference_mode': 'skill_defaults'}],
                               owner='other', sid=self.other_sid)
        self.assertIn('tool_error', cross[0])
        invalid, sent = self.submit([{**self.args(), 'reference_mode': 'selected_skill_images',
                                     'reference_paths': ['assets/does-not-exist.png']}])
        self.assertEqual(len(sent), 1)
        self.assertIn('tool_error', invalid[0])
        self.assertEqual(self.iterations.list(creator_id=self.fixture['creator_id'])['items'], [])
        self.assert_no_worker()

    def test_actual_web_submit_selected_subset(self):
        """TR-RSI-MODE-CHECK-08: real Web turn, not just fabricated runtime context."""
        self.two_skill_images()
        actual = []
        with httpx.Client(base_url=self.url, timeout=15, trust_env=False) as client:
            for color in ('red', 'green', 'purple'):
                image = BytesIO()
                Image.new('RGB', (23, 31), color).save(image, format='PNG')
                raw = image.getvalue()
                response = client.post(f'/api/agent/sessions/{self.sid}/attachments',
                    json={'name': 'same-name.png', 'data_base64': base64.b64encode(raw).decode()})
                self.assertEqual(response.status_code, 201, response.text)
                actual.append((response.json()['id'], raw))
            chosen = [actual[2][0], actual[0][0]]
            provider = ScriptedProvider([[('find', 'discover_tools', {'names': ['manage_skill_iteration.start']})],
                [('start', 'call_discovered_tool', {'name': 'manage_skill_iteration.start',
                    'arguments': {**self.args(), 'reference_mode': 'attachments_only', 'attachment_ids': chosen}})], None])
            provider.client = SimpleNamespace(close=lambda: None)
            self.app.state.chat.provider_factory = lambda: provider
            sent, original = [], StudioClient.request
            def track(client_, method, path, **kwargs):
                if method == 'POST' and path == '/api/skill-iterations':
                    sent.append(deepcopy(kwargs.get('payload')))
                return original(client_, method, path, **kwargs)
            rid = str(uuid4())
            payload = {'request_id': rid, 'text': '只用我本轮选择的两张图片作为优秀参考。',
                'attachment_ids': chosen, 'expected_version': self.app.state.chat.get(self.sid)['version']}
            with patch.dict('os.environ', env('2')), patch.object(StudioClient, 'request', track):
                response = client.post(f'/api/agent/sessions/{self.sid}/turns', json=payload)
                self.assertEqual(response.status_code, 202, response.text)
                completed = wait_idle(client, self.sid)
                replay = client.post(f'/api/agent/sessions/{self.sid}/turns', json=payload)
                self.assertEqual(replay.status_code, 202, replay.text)
            self.assertEqual(completed['status'], 'idle', completed)
            user = next(e for e in completed['entries'] if e.get('kind') == 'user' and e.get('turn_id') == rid)
            self.assertEqual([x['id'] for x in user['attachments']], chosen)
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0]['attachment_ids'], chosen)
            self.assertEqual(sent[0]['reference_paths'], [])
            items = self.iterations.list(creator_id=self.fixture['creator_id'])['items']
            self.assertEqual(len(items), 1)
            row = self.iterations._row(items[0]['id'])
            self.assertEqual([r['sha256'] for r in row.config_json['references']],
                [sha256(actual[2][1]).hexdigest(), sha256(actual[0][1]).hexdigest()])
            self.assertEqual(len(sent), 1)
            self.assertEqual(provider.requests[1][0][:len(provider.requests[0][0])], provider.requests[0][0])
            self.assertEqual(provider.requests[1][1], provider.requests[0][1])
            self.assert_no_worker()
