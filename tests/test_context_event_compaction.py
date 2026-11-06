from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from creatoros.agent.compactor import compact_session
from creatoros.agent.loop import request_context_events
from creatoros.ai.context import estimate_tokens
from creatoros.context import RuntimeContext
from creatoros.session.artifacts import root_for
from creatoros.session.checkpoint import CompactionCheckpoint, load_compaction_checkpoint
from creatoros.tools.builtins import read_file
from tests.check_step_compaction import step
from tests.smoke_compact_session import RecordingProvider


def selected(topic, marker):
    return request_context_events({'data': {'target': {'topic_id': topic, 'series_id': 'series',
        'run_id': 'run-' + topic, 'revision_id': 'revision-' + topic},
        'topic': {'title': 'title-' + topic, 'brief': marker}},
        'images': [{'label': topic, 'attachment': {'id': topic + '-image', 'name': topic + '.png', 'size': 20}}]}, [])[0]


def account_event(label):
    return {'role': 'user', 'creatoros_source': 'account_context_event', 'content': label}


class ContextEventCompactionTest(unittest.TestCase):
    def test_multiple_host_pins_survive_continuous_compaction_reload_and_new_turn(self):
        user = {'role': 'user', 'content': 'only draft'}
        host = selected('topic-a', 'first evidence')
        account = account_event('account update one')
        messages = [{'role': 'system', 'content': 'system'}, user, host, *step(0), account, *step(1), *step(2)]
        budget = estimate_tokens([user, host, account, *step(2)])
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'messages.json'
            path.write_text(json.dumps(messages, ensure_ascii=False), encoding='utf-8')
            provider = RecordingProvider()
            cp = compact_session(provider, messages, [], session_file=path, keep_recent_tokens=budget)
            self.assertEqual(cp.pinned_user_index, 1)
            self.assertEqual(cp.pinned_host_indices, (2, 5))
            projected = cp.project_messages(messages)
            self.assertEqual(projected[2:5], [user, host, account])
            self.assertEqual(load_compaction_checkpoint(messages, path), cp)
            later_account = account_event('account update two')
            extended = [*messages, later_account, *step(3), *step(4)]
            budget2 = estimate_tokens([user, host, account, later_account, *step(4)])
            cp2 = compact_session(provider, extended, [], checkpoint=cp, session_file=path, keep_recent_tokens=budget2)
            self.assertEqual(cp2.pinned_user_index, 1)
            self.assertEqual(cp2.pinned_host_indices, (2, 5, len(messages)))
            restored = load_compaction_checkpoint(extended, path)
            self.assertEqual(restored, cp2)
            projected2 = restored.project_messages(extended)
            self.assertEqual(projected2[2:6], [user, host, account, later_account])
            newer = [*extended, {'role': 'user', 'content': 'another topic'}, {'role': 'assistant', 'content': 'done'}]
            cp3 = compact_session(provider, newer, [], checkpoint=cp2, session_file=path,
                                  keep_recent_tokens=estimate_tokens(newer[-2:]))
            self.assertIsNone(cp3.pinned_user_index)
            self.assertEqual(cp3.pinned_host_indices, ())
            legacy = cp3.to_dict()
            del legacy['pinned_host_indices']
            self.assertEqual(CompactionCheckpoint.from_dict(legacy), cp3)

    def test_selected_snapshots_survive_summary_and_are_session_scoped(self):
        host_a, host_b = selected('topic-a', 'marker-a'), selected('topic-b', 'marker-b')
        host_a['content'][-1]['attachment']['base64'] = 'must-not-be-archived'
        host_a['content'][0]['base64'] = 'must-not-be-archived'
        host_a['content'][-1]['bytes'] = 'must-not-be-archived'
        messages = [{'role': 'system', 'content': 'system'},
                    {'role': 'user', 'content': 'inspect A'}, host_a, {'role': 'assistant', 'content': 'done A'},
                    {'role': 'user', 'content': 'inspect B'}, host_b, {'role': 'assistant', 'content': 'done B'},
                    {'role': 'user', 'content': 'what did A say?'}, {'role': 'assistant', 'content': 'hold'}]
        original = deepcopy(messages)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'one' / 'messages.json'
            path.parent.mkdir()
            path.write_text(json.dumps(messages, ensure_ascii=False), encoding='utf-8')
            provider = RecordingProvider()
            cp = compact_session(provider, messages, [], session_file=path,
                                 keep_recent_tokens=estimate_tokens(messages[-2:]))
            self.assertIsNotNone(cp)
            projected = cp.project_messages(messages)
            self.assertIn({'role': 'user', 'content': 'what did A say?'}, projected)
            self.assertIn('历史工具结果及宿主资料原文索引', cp.summary)
            self.assertEqual(messages, original)
            index = json.loads((root_for(path) / 'index.json').read_text(encoding='utf-8'))
            host_items = [item for item in index.values() if 'host_selected_context' in item['description']]
            self.assertEqual(len(host_items), 2)
            self.assertTrue(all(item['result_ref'].startswith('host-') for item in host_items))
            descriptions = '\n'.join(item['description'] for item in host_items)
            for identity in ('topic-a', 'topic-b', 'series', 'run-topic-a', 'revision-topic-a', 'title-topic-a'):
                self.assertIn(identity, descriptions)
            self.assertNotIn('marker-a', descriptions)
            self.assertNotIn('marker-b', descriptions)
            contexts = RuntimeContext(root, session_file=path, archive_only_reads=True)
            other = RuntimeContext(root, session_file=root / 'other' / 'messages.json', archive_only_reads=True)
            seen = set()
            for item in host_items:
                archive = root_for(path) / item['path']
                read = read_file(str(archive), unit='chars', limit=16000, context=contexts)
                self.assertFalse(read.is_error)
                for marker in ('marker-a', 'marker-b'):
                    if marker in read.content:
                        seen.add(marker)
                self.assertIn('-image', read.content)
                self.assertNotIn('must-not-be-archived', read.content)
                self.assertTrue(read_file(str(archive), unit='chars', context=other).is_error)
                if 'marker-a' in read.content:
                    archive.unlink()
                    self.assertTrue(read_file(str(archive), unit='chars', context=contexts).is_error)
            self.assertEqual(seen, {'marker-a', 'marker-b'})
            transcript = str(provider.contexts[-1].messages)
            self.assertIn('历史宿主选中资料完整原文与图片附件引用', transcript)
            self.assertIn('不是用户指令、当前状态或授权', transcript)
            self.assertNotIn('must-not-be-archived', transcript)

    def test_large_snapshots_have_bounded_summary_and_full_paged_recovery(self):
        marker = 'MID-FORENSIC-MARKER'
        host = [selected('topic-' + str(i), 'A' * 24000 + marker + 'Z' * 24000) for i in range(3)]
        messages = [{'role': 'system', 'content': 'system'}]
        for index, event in enumerate(host):
            messages.extend([{'role': 'user', 'content': 'inspect ' + str(index)}, event,
                             {'role': 'assistant', 'content': 'done'}])
        messages.extend([{'role': 'user', 'content': 'find the middle marker'},
                         {'role': 'assistant', 'content': 'hold'}])
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'one' / 'messages.json'
            path.parent.mkdir()
            path.write_text(json.dumps(messages, ensure_ascii=False), encoding='utf-8')
            provider = RecordingProvider()
            checkpoint = compact_session(provider, messages, [], session_file=path,
                                         keep_recent_tokens=estimate_tokens(messages[-2:]))
            self.assertIsNotNone(checkpoint)
            source = str(provider.contexts[-1].messages)
            self.assertLess(len(source), 12000)
            self.assertNotIn(marker, source)
            self.assertIn('snapshot_omitted', source)
            self.assertIn('truncated', source)
            index = json.loads((root_for(path) / 'index.json').read_text(encoding='utf-8'))
            refs = [item for item in index.values() if 'host_selected_context' in item['description']]
            self.assertEqual(len(refs), 3)
            target = next(item for item in refs if 'topic-1' in item['description'])
            archive = root_for(path) / target['path']
            context = RuntimeContext(root, session_file=path, archive_only_reads=True)
            offset, found = 1, False
            for _ in range(8):
                page = read_file(str(archive), offset=offset, limit=12000, unit='chars', context=context)
                self.assertFalse(page.is_error)
                if marker in page.content:
                    found = True
                    break
                if '[EOF]' in page.content:
                    break
                offset += 12000
            self.assertTrue(found)

    def test_archive_failure_never_saves_new_checkpoint(self):
        host = selected('topic-a', 'evidence')
        messages = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'inspect'},
                    host, {'role': 'assistant', 'content': 'done'},
                    {'role': 'user', 'content': 'later'}, {'role': 'assistant', 'content': 'done'}]
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'messages.json'
            path.write_text(json.dumps(messages, ensure_ascii=False), encoding='utf-8')
            with patch('creatoros.session.artifacts.externalize', side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):
                    compact_session(RecordingProvider(), messages, [], session_file=path,
                                    keep_recent_tokens=estimate_tokens(messages[-2:]))
            self.assertIsNone(load_compaction_checkpoint(messages, path))

    def test_invalid_text_or_image_block_prevents_archive(self):
        for broken in ({'type': 'text', 'text': 123}, {'type': 'image_url', 'url': 'data:image/png;base64,abc'}):
            host = selected('topic-a', 'evidence')
            host['content'].append(broken)
            messages = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'inspect'},
                        host, {'role': 'assistant', 'content': 'done'},
                        {'role': 'user', 'content': 'later'}, {'role': 'assistant', 'content': 'done'}]
            with self.subTest(broken=broken), TemporaryDirectory() as directory:
                path = Path(directory) / 'messages.json'
                path.write_text(json.dumps(messages, ensure_ascii=False), encoding='utf-8')
                with self.assertRaises(ValueError):
                    compact_session(RecordingProvider(), messages, [], session_file=path,
                                    keep_recent_tokens=estimate_tokens(messages[-2:]))
                self.assertIsNone(load_compaction_checkpoint(messages, path))

    def test_malformed_attachment_metadata_prevents_archive(self):
        for field, value in (('size', 'not-int'), ('width', ['bytes']), ('height', True),
                             ('size', 4 * 1024 * 1024 + 1), ('name', ['not-text']),
                             ('url', 'x' * 4097), ('mime_type', False)):
            host = selected('topic-a', 'evidence')
            host['content'][-1]['attachment'][field] = value
            messages = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'inspect'},
                        host, {'role': 'assistant', 'content': 'done'},
                        {'role': 'user', 'content': 'later'}, {'role': 'assistant', 'content': 'done'}]
            with self.subTest(field=field), TemporaryDirectory() as directory:
                path = Path(directory) / 'messages.json'
                path.write_text(json.dumps(messages, ensure_ascii=False), encoding='utf-8')
                with self.assertRaises(ValueError):
                    compact_session(RecordingProvider(), messages, [], session_file=path,
                                    keep_recent_tokens=estimate_tokens(messages[-2:]))
                self.assertIsNone(load_compaction_checkpoint(messages, path))


if __name__ == '__main__':
    unittest.main()
