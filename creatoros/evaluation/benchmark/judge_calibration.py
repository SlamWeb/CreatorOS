"""Frozen semantic-Judge pilot. Explicitly paid; never reruns claimed samples.

Synthetic examples are not human calibration and do not measure general accuracy.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

from creatoros.evaluation.benchmark.metrics import aggregate_metrics
from creatoros.evaluation.benchmark.semantic_judge import evaluate_judge
from creatoros.evaluation.benchmark.judge_timing import qualify_temporal_judgments
from creatoros.evaluation.benchmark.visual_guard import qualify_visual_judgments

REPO = Path(__file__).resolve().parents[3]
DATASET = REPO / 'tests/fixtures/benchmark_judge_calibration_v2.json'
SOURCES = [Path(__file__), REPO / 'creatoros/evaluation/benchmark/server.py', REPO / 'creatoros/evaluation/benchmark/semantic_judge.py',
           REPO / 'creatoros/evaluation/benchmark/scoring.py',
           REPO / 'creatoros/evaluation/benchmark/judge_timing.py', REPO / 'creatoros/evaluation/benchmark/visual_guard.py',
           REPO / 'creatoros/evaluation/benchmark/capture.py', REPO / 'creatoros/evaluation/benchmark/metrics.py',
           REPO / 'creatoros/ai/deepseek.py', REPO / 'creatoros/ai/context.py']


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_dataset(data):
    samples = data.get('samples')
    if not isinstance(samples, list) or not samples:
        raise ValueError('Empty calibration dataset')
    ids = set()
    for row in samples:
        identifier = row.get('id', '')
        if not identifier or not identifier.replace('-', '').isalnum() or identifier in ids:
            raise ValueError('Sample IDs must be unique safe alphanumeric/hyphen strings')
        ids.add(identifier)
        if row.get('split') not in {'development', 'holdout'}:
            raise ValueError('Invalid split')
        rules = {r['id'] for r in row['case']['rubric']}
        if set(row['expected']) != rules or any(v is not None and type(v) is not bool for v in row['expected'].values()):
            raise ValueError('Expected labels must cover all rubric IDs with booleans or null')
        if row['payload'].get('rubric') != row['case']['rubric']:
            raise ValueError('Payload and case rubric differ')
    if {r['split'] for r in samples} != {'development', 'holdout'}:
        raise ValueError('Separate development and holdout samples required')
    return data


def _path(root):
    root = Path(root).resolve()
    allowed = (REPO / 'tmp').resolve()
    if root == allowed or not root.is_relative_to(allowed):
        raise ValueError('Use a new directory under project tmp')
    return root


def _write(path, data):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def freeze(root, dataset=DATASET):
    root = _path(root)
    data = validate_dataset(json.loads(Path(dataset).read_text(encoding='utf-8')))
    root.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(dataset, root / 'dataset.json')
    _write(root / 'manifest.json', {'dataset_id': data['dataset_id'], 'dataset_sha256': digest(root / 'dataset.json'),
        'source_sha256': {str(p.relative_to(REPO)): digest(p) for p in SOURCES},
        'created_at': datetime.now(timezone.utc).isoformat(),
        'model': 'deepseek-flash', 'max_retries': 0, 'pricing': None,
        'limitations': 'Synthetic AI-labelled pilot; held-out examples are not a human or representative accuracy benchmark. Cost unavailable without frozen pricing.'})
    return root


def verify(root):
    root = _path(root)
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    if digest(root / 'dataset.json') != manifest['dataset_sha256']:
        raise ValueError('Frozen calibration dataset changed')
    for name, expected in manifest['source_sha256'].items():
        if digest(REPO / name) != expected:
            raise ValueError('Frozen source changed: ' + name)
    return manifest, validate_dataset(json.loads((root / 'dataset.json').read_text(encoding='utf-8')))


def compare(expected, actual):
    items = {item['id']: item for item in actual.get('items', [])}
    rows = []
    for identifier, label in expected.items():
        item = items.get(identifier)
        value = item.get('pass') if item else None
        rows.append({'id': identifier, 'expected': label, 'actual': value,
                     'match': value is label if item else False,
                     'classification': ('missing_response' if item is None else 'match' if value is label else
                         'false_pass' if value is True and label is False else
                         'false_fail' if value is False and label is True else 'uncertainty_mismatch')})
    return rows


def run(root, split, *, allow_paid=False, provider_factory=None):
    if not allow_paid:
        raise ValueError('Explicit --allow-paid is required')
    root = _path(root)
    manifest, dataset = verify(root)
    selected = [x for x in dataset['samples'] if x['split'] == split]
    if not selected:
        raise ValueError('No samples in requested split')
    summary = []
    for sample in selected:
        verify(root)
        directory = root / sample['id']
        # Claim before request: an interruption or unknown outcome cannot repeat a paid call.
        directory.mkdir(exist_ok=False)
        _write(directory / 'input.json', {'case': sample['case'], 'payload': sample['payload'],
               'expected': sample['expected'], 'source': sample['source'], 'basis': sample['basis']})
        records = []
        def sink(row):
            # MeasuredProvider calls once before and once after; retain latest record.
            for i, old in enumerate(records):
                if old['request_id'] == row['request_id']:
                    records[i] = dict(row)
                    break
            else:
                records.append(dict(row))
            (directory / 'request-receipt.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
        result, error = None, None
        try:
            result = evaluate_judge(sample['case'], sample['payload'], [], directory, sink, provider_factory=provider_factory)
        except Exception as exc:
            error = {'type': type(exc).__name__, 'message': str(exc)}
        qualified = qualify_visual_judgments(sample['case'], qualify_temporal_judgments(sample['case'], result)) if result else {}
        rows = compare(sample['expected'], qualified)
        item = {'sample_id': sample['id'], 'split': split, 'checks': rows, 'judge': result,
                'qualified_judge': qualified, 'error': error,
                'metrics': aggregate_metrics(records, manifest['pricing'])}
        _write(directory / 'result.json', item)
        summary.append(item)
    out = root / (split + '-summary.json')
    _write(out, {'results': summary, 'source': 'AI-constructed labels, not human calibration',
                 'exact_matches': sum(r['match'] for x in summary for r in x['checks']),
                 'total_labels': sum(len(x['checks']) for x in summary)})
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['freeze', 'run'])
    parser.add_argument('--root', required=True)
    parser.add_argument('--split', choices=['development', 'holdout'])
    parser.add_argument('--allow-paid', action='store_true')
    args = parser.parse_args()
    if args.action == 'freeze':
        freeze(args.root)
    else:
        if not args.split:
            parser.error('--split is required')
        results = run(args.root, args.split, allow_paid=args.allow_paid)
        if any(item['error'] or any(not row['match'] for row in item['checks']) for item in results):
            raise SystemExit(1)


if __name__ == '__main__':
    main()
