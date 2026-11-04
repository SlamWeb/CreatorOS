"""Opt-in real multimodal Judge pilot using frozen, locally generated images.

Three examples compare the same two image roles with correct/swapped colour
claims and with no images delivered. This is not human calibration. Never
reruns a claimed model call. No business services or production files are used.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import zipfile

from PIL import Image

from .metrics import aggregate_metrics
from .semantic_judge import evaluate_judge
from .visual_evidence import collect_visual_evidence, load_visual_attachments
from .visual_guard import qualify_visual_judgments


REPO = Path(__file__).resolve().parents[3]
SOURCES = tuple(REPO / p for p in (
    'creatoros/evaluation/benchmark/visual_judge_pilot.py',
    'creatoros/evaluation/benchmark/visual_evidence.py',
    'creatoros/evaluation/benchmark/visual_guard.py',
    'creatoros/evaluation/benchmark/semantic_judge.py',
    'creatoros/evaluation/benchmark/scoring.py',
    'creatoros/evaluation/benchmark/judge_timing.py',
    'creatoros/evaluation/benchmark/capture.py',
    'creatoros/evaluation/benchmark/metrics.py',
    'creatoros/ai/deepseek.py', 'creatoros/ai/context.py'))

SAMPLES = (
    {'id': 'correct', 'answer': '第一张成品主体是蓝色，第二张成品主体是红色。', 'expected': True},
    {'id': 'swapped', 'answer': '第一张成品主体是红色，第二张成品主体是蓝色。', 'expected': False},
    {'id': 'role-swapped', 'answer': '第一张成品主体是蓝色，第二张成品主体是红色。', 'expected': False},
    {'id': 'missing', 'answer': '第一张成品主体是蓝色，第二张成品主体是红色。', 'expected': None},
)
CASE = {'id': 'B45', 'rubric': [{'id': 'b45-s1',
    'question': '回答对第一张及第二张成品主体颜色的描述，是否都与实际对应图片一致？看不到足够图片时返回null，不要根据文字猜测。',
    'critical': True}]}


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _root(value):
    root = Path(value).resolve()
    tmp = (REPO / 'tmp').resolve()
    if root == tmp or not root.is_relative_to(tmp):
        raise ValueError('Choose a new isolated directory beneath project tmp')
    return root


def _write(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def freeze(path):
    root = _root(path)
    root.mkdir(parents=True, exist_ok=False)
    world = root / 'world'
    (world / 'inputs').mkdir(parents=True)
    (world / 'outputs' / 'run').mkdir(parents=True)
    blue = Image.new('RGB', (128, 128), (25, 75, 220))
    red = Image.new('RGB', (128, 128), (220, 35, 30))
    blue.save(world/'inputs/reference.png')
    blue.save(world/'outputs/run/first.png')
    red.save(world/'outputs/run/second.png')
    files = {p.relative_to(world).as_posix(): _digest(p) for p in world.rglob('*.png')}
    uploaded = files['inputs/reference.png']
    data = {'variables': {'creator_a':'pilot-account','run_a':'pilot-run'},
        'before': {'files':files, 'database':{
            'content_runs':[{'id':'pilot-run','active_revision_number':1}],
            'content_revisions':[{'id':'pilot-revision','content_run_id':'pilot-run','revision_number':1,
                'artifact_directory':str(world/'outputs/run'),
                'production_input_json':{'creator_id':'pilot-account'},
                'validation_json':{'images':[{'path':'first.png','sha256':files['outputs/run/first.png'],'order':1},
                    {'path':'second.png','sha256':files['outputs/run/second.png'],'order':2}]}}],
            'production_tasks':[]}},
        'after': {'files':files, 'database':{}},
        'browser': {'session_id':'pilot-session','turns':[{'session_id':'pilot-session','turn_id':'pilot-turn',
            'accepted':{'id':'pilot-session','entries':[{'kind':'user','turn_id':'pilot-turn','attachments':[{'id':uploaded}]}]}}]}}
    binding = collect_visual_evidence(world, CASE, data)
    if binding['status'] != 'complete' or len(binding['bindings']) != 3:
        raise ValueError('Cannot build a complete pilot visual manifest')
    _write(root/'bindings.json', binding)
    # Independently change the input revision's role/order assignment: the red
    # pixels now belong to image 1, the blue pixels to image 2. The model must
    # follow identities rather than relying on the filename or previous example.
    swapped_data = deepcopy(data)
    items = swapped_data['before']['database']['content_revisions'][0]['validation_json']['images']
    items[0]['order'], items[1]['order'] = 2, 1
    swapped_binding = collect_visual_evidence(world, CASE, swapped_data)
    if swapped_binding['status'] != 'complete':
        raise ValueError('Cannot build the swapped-role visual manifest')
    _write(root/'bindings-role-swapped.json', swapped_binding)
    _write(root/'samples.json', {'case':CASE, 'samples':SAMPLES,
        'limitations':'Synthetic unambiguous colours; no human calibration, no general aesthetic claim.'})
    # Keep the exact source bytes, not only their hashes. Write a deterministic
    # archive so later workspace changes cannot erase the experiment inputs.
    source_bytes = {p.relative_to(REPO).as_posix(): p.read_bytes() for p in SOURCES}
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(source_bytes.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    (root/'source-snapshot.zip').write_bytes(archive_bytes.getvalue())
    _write(root/'manifest.json', {'schema_version':1, 'created_at':datetime.now(timezone.utc).isoformat(),
        'sources': {name:hashlib.sha256(data).hexdigest() for name, data in source_bytes.items()},
        'source_snapshot': {'path':'source-snapshot.zip', 'sha256':_digest(root/'source-snapshot.zip'),
                            'members':sorted(source_bytes)},
        'inputs': {p.relative_to(root).as_posix():_digest(p) for p in sorted(root.rglob('*')) if p.is_file()},
        'model':'deepseek-flash', 'max_retries':0, 'pricing':None,
        'limitations':'Three deliberately simple AI-authored examples; not a blind test or a representative quality score.'})
    return root


def verify(path):
    root = _root(path)
    manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    snapshot = manifest.get('source_snapshot')
    if not isinstance(snapshot, dict) or snapshot.get('path') != 'source-snapshot.zip':
        raise ValueError('Missing or invalid source snapshot')
    archive_path = root/'source-snapshot.zip'
    if _digest(archive_path) != snapshot.get('sha256'):
        raise ValueError('Frozen source snapshot changed')
    try:
        with zipfile.ZipFile(archive_path) as archive:
            if archive.namelist() != snapshot.get('members') or set(archive.namelist()) != set(manifest['sources']):
                raise ValueError('Frozen source snapshot member set changed')
            for name in archive.namelist():
                if hashlib.sha256(archive.read(name)).hexdigest() != manifest['sources'][name]:
                    raise ValueError('Frozen source snapshot member changed: '+name)
    except zipfile.BadZipFile as error:
        raise ValueError('Invalid frozen source snapshot') from error
    for name, value in manifest['sources'].items():
        if _digest(REPO/name) != value:
            raise ValueError('Pilot source changed: '+name)
    for name, value in manifest['inputs'].items():
        path = root/name
        if not path.resolve().is_relative_to(root) or _digest(path) != value:
            raise ValueError('Frozen pilot input changed: '+name)
    return manifest


def run(path, *, allow_paid=False, provider_factory=None):
    if not allow_paid:
        raise ValueError('Explicit --allow-paid is required')
    root = _root(path)
    manifest = verify(root)
    dataset = json.loads((root/'samples.json').read_text(encoding='utf-8'))
    saved_binding = json.loads((root/'bindings.json').read_text(encoding='utf-8'))
    results = []
    for sample in dataset['samples']:
        verify(root)
        directory = root/sample['id']
        directory.mkdir(exist_ok=False)  # exclusive claim before any request
        visual = (json.loads((root/'bindings-role-swapped.json').read_text(encoding='utf-8'))
                  if sample['id'] == 'role-swapped' else dict(saved_binding))
        if sample['id'] == 'missing':
            visual.update(status='needs_review', bindings=[], errors=['Deliberate no-image input'])
            images = []
        else:
            images = load_visual_attachments(root/'world', visual)
        payload = {'task':[{'kind':'user','text':'核对两张成品图片的主体颜色。'}],
            'intent':'核对可见颜色', 'expected_outcome':'判断回答是否符合对应图片。',
            'answer':sample['answer'], 'conversation':[{'role':'assistant','content':sample['answer']}],
            'rubric':dataset['case']['rubric'], 'temporal_evidence':{}, 'visual_evidence':visual}
        _write(directory/'input.json', {'case':dataset['case'], 'payload':payload,
            'expected':sample['expected'], 'source':'synthetic pilot', 'image_bytes_in_manifest':True})
        records = []
        def sink(record):
            current = dict(record)
            for index, previous in enumerate(records):
                if previous['request_id'] == current['request_id']:
                    records[index] = current
                    break
            else:
                records.append(current)
            (directory/'request-receipt.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
        judged, qualified, error = None, None, None
        try:
            judged = evaluate_judge(dataset['case'], payload, [], root/'world', sink,
                provider_factory=provider_factory, visual_manifest=visual, visual_attachments=images)
            qualified = qualify_visual_judgments(dataset['case'], judged)
        except Exception as exc:
            error = {'type':type(exc).__name__, 'message':str(exc)}
        raw = judged['items'][0]['pass'] if judged else None
        assessed = qualified['items'][0]['pass'] if qualified else None
        result = {'id':sample['id'],'expected':sample['expected'], 'raw':raw, 'qualified':assessed,
            'raw_matched':error is None and raw is sample['expected'],
            'matched':error is None and assessed is sample['expected'], 'judge':judged,
            'qualified_judge':qualified,'error':error,'metrics':aggregate_metrics(records, manifest['pricing'])}
        _write(directory/'result.json', result)
        results.append(result)
    _write(root/'summary.json', {'results':results, 'matched':sum(r['matched'] for r in results),
        'count':len(results), 'limitations':manifest['limitations']})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['freeze','run'])
    parser.add_argument('--root', required=True)
    parser.add_argument('--allow-paid', action='store_true')
    args = parser.parse_args()
    if args.action == 'freeze':
        freeze(args.root)
    else:
        results = run(args.root, allow_paid=args.allow_paid)
        if any(not row['matched'] for row in results):
            raise SystemExit(1)


if __name__ == '__main__':
    main()
