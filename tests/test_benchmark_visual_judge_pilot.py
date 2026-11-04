"""Exercise the multimodal pilot without paying for a model call."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
import zipfile
from unittest.mock import patch

from creatoros.evaluation.benchmark.visual_judge_pilot import REPO, freeze, run, verify


class FakeVisionProvider:
    calls = []
    texts = []
    responses = [True, False, False, True]

    def __init__(self, **kwargs):
        self.sink = kwargs['sink']

    def complete(self, context):
        messages, _ = context.to_request()
        blocks = messages[-1]['content']
        if isinstance(blocks, str):
            blocks = []
        attachments = [part['attachment'] for part in blocks if part.get('type') == 'image_ref']
        self.texts.append([part['text'] for part in blocks if part.get('type') == 'text'])
        images = [self.image_resolver(attachment) for attachment in attachments]
        self.calls.append(images)
        self.sink({'request_id':str(len(self.calls)), 'model':'fake', 'purpose':'judge',
                   'input_tokens':10, 'output_tokens':2, 'cache_hit_tokens':0, 'cache_miss_tokens':10})
        return SimpleNamespace(content=json.dumps({'items':[{'id':'b45-s1',
            'pass':self.responses[len(self.calls)-1], 'evidence':'controlled fixture'}]}))


class VisualJudgePilotTest(unittest.TestCase):
    def test_freeze_send_roles_and_missing_image_guard(self):
        (REPO/'tmp').mkdir(exist_ok=True)
        FakeVisionProvider.calls = []
        FakeVisionProvider.texts = []
        with TemporaryDirectory(dir=REPO/'tmp') as parent:
            root = Path(parent)/'pilot'
            freeze(root)
            manifest = verify(root)
            self.assertEqual(manifest['model'], 'deepseek-flash')
            with self.assertRaisesRegex(ValueError, 'allow-paid'):
                run(root, provider_factory=FakeVisionProvider)
            out = run(root, allow_paid=True, provider_factory=FakeVisionProvider)
            self.assertEqual([r['id'] for r in out], ['correct','swapped','role-swapped','missing'])
            self.assertTrue(all(row['matched'] for row in out))
            self.assertEqual([len(row) for row in FakeVisionProvider.calls], [3,3,3,0])
            self.assertTrue(any('user_attachment 是用户提供的参考图' in text for text in FakeVisionProvider.texts[2]))
            self.assertEqual(out[-1]['raw'], True)
            self.assertIsNone(out[-1]['qualified'])
            self.assertFalse(out[-1]['raw_matched'])
            self.assertTrue(out[-1]['matched'])
            self.assertEqual(out[0]['judge']['visual_evidence']['sent_ids'], ['image-1','image-2','image-3'])
            original = out[0]['judge']['visual_evidence']['bindings']
            swapped = out[2]['judge']['visual_evidence']['bindings']
            self.assertEqual([x.get('image_order') for x in original if x['role']=='run_image'], [1,2])
            self.assertEqual([x.get('image_order') for x in swapped if x['role']=='run_image'], [1,2])
            self.assertEqual(original[0]['sha256'], swapped[0]['sha256'])
            self.assertEqual(original[1]['sha256'], swapped[2]['sha256'])
            self.assertEqual(original[2]['sha256'], swapped[1]['sha256'])
            with self.assertRaises(FileExistsError):
                run(root, allow_paid=True, provider_factory=FakeVisionProvider)

    def test_frozen_image_tampering_rejected(self):
        (REPO/'tmp').mkdir(exist_ok=True)
        with TemporaryDirectory(dir=REPO/'tmp') as parent:
            root = Path(parent)/'pilot'
            freeze(root)
            (root/'world/outputs/run/first.png').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'input changed'):
                verify(root)

    def test_source_snapshot_keeps_original_bytes_and_detects_tampering(self):
        import creatoros.evaluation.benchmark.visual_judge_pilot as pilot
        (REPO/'tmp').mkdir(exist_ok=True)
        with TemporaryDirectory(dir=REPO/'tmp') as parent:
            source = Path(parent)/'source.py'
            source.write_text('original source', encoding='utf8')
            root = Path(parent)/'pilot'
            with patch.object(pilot, 'SOURCES', (source,)):
                freeze(root)
                info = verify(root)
                with zipfile.ZipFile(root/'source-snapshot.zip') as archive:
                    self.assertEqual(archive.read(source.relative_to(REPO).as_posix()), b'original source')
                source.write_text('changed source', encoding='utf8')
                with self.assertRaisesRegex(ValueError, 'source changed'):
                    verify(root)
                with zipfile.ZipFile(root/'source-snapshot.zip') as archive:
                    self.assertEqual(archive.read(source.relative_to(REPO).as_posix()), b'original source')
                (root/'source-snapshot.zip').write_bytes(b'tampered')
                with self.assertRaisesRegex(ValueError, 'snapshot changed'):
                    verify(root)


if __name__ == '__main__': unittest.main()
