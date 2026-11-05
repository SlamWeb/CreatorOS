"""Frozen semantic-Judge pilot. Explicitly paid; never reruns claimed samples.

Synthetic examples are not human calibration and do not measure general accuracy.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import stat

from creatoros.evaluation.benchmark.metrics import aggregate_metrics
from creatoros.evaluation.benchmark.semantic_judge import evaluate_judge
from creatoros.evaluation.benchmark.judge_timing import qualify_temporal_judgments
from creatoros.evaluation.benchmark.judge_contradiction_guard import qualify_explicit_contradictions
from creatoros.evaluation.benchmark.visual_guard import qualify_visual_judgments
from creatoros.evaluation.benchmark.grounding_guard import qualify_grounding_judgments
from creatoros.evaluation.benchmark.program_evidence_guard import (
    VERSION as PROGRAM_EVIDENCE_GUARD_VERSION, qualify_program_judgments,
)

REPO = Path(__file__).resolve().parents[3]
DATASET = REPO / 'tests/fixtures/benchmark_judge_calibration_v3.json'
ADDENDUM_DATASET = REPO / 'tests/fixtures/benchmark_judge_calibration_v4_addendum.json'
PROGRAM_GUARD_DATASET = REPO / 'tests/fixtures/benchmark_judge_calibration_v5_program_guard.json'
CASE_DEFINITIONS = REPO / 'docs/agent-eval/benchmark-v2/cases.json'
SOURCES = [Path(__file__), REPO / 'creatoros/evaluation/benchmark/server.py', REPO / 'creatoros/evaluation/benchmark/semantic_judge.py',
           REPO / 'creatoros/evaluation/benchmark/scoring.py',
           REPO / 'creatoros/evaluation/benchmark/judge_contradiction_guard.py',
           REPO / 'creatoros/evaluation/benchmark/judge_timing.py', REPO / 'creatoros/evaluation/benchmark/visual_guard.py',
           REPO / 'creatoros/evaluation/benchmark/visual_evidence.py',
           REPO / 'creatoros/evaluation/benchmark/grounding_guard.py', REPO / 'creatoros/evaluation/benchmark/research_evidence.py',
           REPO / 'creatoros/evaluation/benchmark/rsi_evidence.py', REPO / 'creatoros/evaluation/benchmark/video_evidence.py',
           REPO / 'creatoros/evaluation/benchmark/program_evidence_guard.py',
           REPO / 'creatoros/evaluation/benchmark/capture.py', REPO / 'creatoros/evaluation/benchmark/metrics.py',
           REPO / 'creatoros/ai/deepseek.py', REPO / 'creatoros/ai/context.py', CASE_DEFINITIONS]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical_json_digest(value):
    """Ignore line-ending/indentation differences without ignoring JSON data."""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def _checked_member(root, name):
    """Return a regular in-tree file without following links or junctions."""
    relative = Path(name)
    if relative.is_absolute() or relative.drive or not relative.parts or '..' in relative.parts:
        raise ValueError('Unsafe source snapshot path')
    path = Path(root)
    for part in relative.parts:
        path = path / part
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Linked source snapshot path: ' + str(relative))
    if not path.is_file():
        raise ValueError('Source snapshot member is not a regular file: ' + str(relative))
    return path


def _source_name(path):
    relative = Path(path).relative_to(REPO)
    if relative.suffix not in {'.py', '.json'} or relative.name.lower().startswith('.env'):
        raise ValueError('Only explicitly listed non-secret source files can be frozen')
    return relative.as_posix()


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
        if not isinstance(row.get('source'), str) or not row['source'].strip() or not isinstance(row.get('basis'), str) or not row['basis'].strip():
            raise ValueError('Every reference label needs a source and explicit basis')
        answer = row['payload'].get('answer')
        conversation = row['payload'].get('conversation')
        assistant_replies = [turn.get('content') for turn in conversation if isinstance(turn, dict)
                             and turn.get('role') == 'assistant'] if isinstance(conversation, list) else []
        if not isinstance(answer, str) or not assistant_replies or any(
                not isinstance(reply, str) or not reply or reply not in answer for reply in assistant_replies):
            raise ValueError('Answer must contain every recorded assistant reply')
    if {r['split'] for r in samples} != {'development', 'holdout'}:
        raise ValueError('Separate development and holdout samples required')
    if data.get('case_id_disjoint'):
        development = {r['case']['id'] for r in samples if r['split'] == 'development'}
        holdout = {r['case']['id'] for r in samples if r['split'] == 'holdout'}
        if development & holdout:
            raise ValueError('A case ID cannot cross case-ID-disjoint splits')
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
    if data.get('source_dataset_sha256') or data.get('source_dataset_canonical_sha256'):
        current = json.loads(CASE_DEFINITIONS.read_text(encoding='utf-8'))
        actual = (canonical_json_digest(current) if data.get('source_dataset_canonical_sha256')
                  else digest(CASE_DEFINITIONS))
        expected = data.get('source_dataset_canonical_sha256') or data['source_dataset_sha256']
        if actual != expected:
            raise ValueError('Calibration labels refer to a different case definition; review and regenerate')
        if current.get('dataset_id') != data.get('source_dataset_id'):
            raise ValueError('Calibration case dataset ID mismatch')
        cases = {row['id']: row for row in current['cases']}
        for row in data['samples']:
            source_case = cases.get(row['case']['id'], {})
            full = source_case.get('rubric', [])
            payload = row['payload']
            if any(payload.get(target) != source_case.get(source) for target, source in
                   (('task', 'steps'), ('intent', 'intent'), ('expected_outcome', 'expected_outcome'))):
                raise ValueError('Calibration task or intent differs from the source case')
            user_queries = [step['text'] for step in source_case.get('steps', []) if step.get('kind') == 'user']
            if payload.get('actual_queries') != user_queries:
                raise ValueError('Calibration queries differ from the source case')
            if row.get('reference_scope', 'full_case') == 'isolated_rule':
                if len(row['case']['rubric']) != 1 or row['case']['rubric'][0] not in full:
                    raise ValueError('Isolated calibration rule differs from the source case')
            elif row['case']['rubric'] != full:
                raise ValueError('Calibration rubric differs from the source case')
    root.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(dataset, root / 'dataset.json')
    snapshot = root / 'source-snapshot'
    snapshot.mkdir()
    source_hashes = {}
    for source in SOURCES:
        name = _source_name(source)
        if name in source_hashes:
            raise ValueError('Duplicate frozen source: ' + name)
        body = _checked_member(REPO, name).read_bytes()
        destination = snapshot / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as stream:
            stream.write(body)
        source_hashes[name] = hashlib.sha256(body).hexdigest()
    if any(digest(_checked_member(REPO, name)) != expected for name, expected in source_hashes.items()):
        raise ValueError('Source changed while freezing')
    if data.get('source_dataset_canonical_sha256') and CASE_DEFINITIONS in SOURCES:
        frozen_cases = json.loads((snapshot / _source_name(CASE_DEFINITIONS)).read_text(encoding='utf-8'))
        if canonical_json_digest(frozen_cases) != data['source_dataset_canonical_sha256']:
            raise ValueError('Case definitions changed while freezing')
    _write(root / 'manifest.json', {'dataset_id': data['dataset_id'], 'dataset_sha256': digest(root / 'dataset.json'),
        'source_sha256': source_hashes,
        'source_snapshot': {'policy': 'required-v1', 'directory': 'source-snapshot', 'file_sha256': source_hashes},
        'program_evidence_guard_version': PROGRAM_EVIDENCE_GUARD_VERSION,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'model': 'deepseek-flash', 'max_retries': 0, 'pricing': None,
        'limitations': data.get('limitations', 'Synthetic AI-labelled pilot; not human calibration. Cost unavailable without frozen pricing.')})
    verify(root)
    return root


def verify(root):
    root = _path(root)
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    if digest(root / 'dataset.json') != manifest['dataset_sha256']:
        raise ValueError('Frozen calibration dataset changed')
    if manifest.get('program_evidence_guard_version') != PROGRAM_EVIDENCE_GUARD_VERSION:
        raise ValueError('Frozen program evidence qualification version changed or is missing')
    snapshot = manifest.get('source_snapshot')
    if snapshot is not None and (not isinstance(snapshot, dict) or snapshot.get('policy') != 'required-v1'
            or snapshot.get('directory') != 'source-snapshot' or snapshot.get('file_sha256') != manifest['source_sha256']):
        raise ValueError('Invalid frozen source snapshot manifest')
    for name, expected in manifest['source_sha256'].items():
        if digest(_checked_member(REPO, name)) != expected:
            raise ValueError('Frozen source changed: ' + name)
        if snapshot is not None:
            try:
                member = _checked_member(root, 'source-snapshot/' + name)
            except (OSError, ValueError) as error:
                raise ValueError('Frozen source snapshot unavailable: ' + name) from error
            if digest(member) != expected:
                raise ValueError('Frozen source snapshot changed: ' + name)
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


def run(root, split, *, allow_paid=False, provider_factory=None, resume=False):
    if not allow_paid:
        raise ValueError('Explicit --allow-paid is required')
    root = _path(root)
    manifest, dataset = verify(root)
    selected = [x for x in dataset['samples'] if x['split'] == split]
    if not selected:
        raise ValueError('No samples in requested split')
    out = root / (split + '-summary.json')
    if out.exists():
        raise FileExistsError('Split summary already exists; create a new freeze')
    summary = []
    for sample in selected:
        verify(root)
        directory = root / sample['id']
        if directory.exists() and resume:
            input_path, result_path = directory / 'input.json', directory / 'result.json'
            expected_input = {'case': sample['case'], 'payload': sample['payload'],
                              'expected': sample['expected'], 'source': sample['source'], 'basis': sample['basis']}
            if input_path.exists() and json.loads(input_path.read_text(encoding='utf-8')) != expected_input:
                raise ValueError('Claimed sample input differs from frozen sample: ' + sample['id'])
            if result_path.exists():
                saved = json.loads(result_path.read_text(encoding='utf-8'))
                if not input_path.exists() or saved.get('sample_id') != sample['id'] or saved.get('split') != split:
                    raise ValueError('Claimed sample result identity or input is missing: ' + sample['id'])
                summary.append(saved)
            else:
                # A claimed but unfinished request may have reached the provider.
                # Never invoke it again just because a local receipt is missing.
                summary.append({'sample_id': sample['id'], 'split': split,
                    'checks': compare(sample['expected'], {}), 'judge': None, 'qualified_judge': {},
                    'error': {'type': 'UnresolvedClaim', 'message': 'Previously claimed; outcome unknown and not retried'},
                    'metrics': aggregate_metrics([], manifest['pricing'])})
            continue
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
        qualified = qualify_program_judgments(sample['case'], qualify_grounding_judgments(
            sample['case'], qualify_visual_judgments(sample['case'],
                qualify_temporal_judgments(sample['case'], qualify_explicit_contradictions(sample['case'], result)))),
            sample['payload'].get('state_checks')) if result else {}
        rows = compare(sample['expected'], qualified)
        item = {'sample_id': sample['id'], 'split': split, 'checks': rows, 'judge': result,
                'raw_checks': compare(sample['expected'], result or {}),
                'reference_scope': sample.get('reference_scope', 'full_case'),
                'coverage_kind': sample.get('coverage_kind', 'semantic_judge'),
                'qualified_judge': qualified, 'error': error,
                'metrics': aggregate_metrics(records, manifest['pricing'])}
        _write(directory / 'result.json', item)
        summary.append(item)
        print(json.dumps({'sample_id': sample['id'], 'split': split,
                          'matches': sum(row['match'] for row in rows), 'total': len(rows),
                          'error': bool(error)}, ensure_ascii=False), flush=True)
    _write(out, {'results': summary, 'source': 'AI-constructed labels, not human calibration',
                 'limitations': dataset.get('limitations'),
                 'exact_matches': sum(r['match'] for x in summary for r in x['checks']),
                 'total_labels': sum(len(x['checks']) for x in summary)})
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['freeze', 'run'])
    parser.add_argument('--root', required=True)
    parser.add_argument('--split', choices=['development', 'holdout'])
    parser.add_argument('--dataset', choices=['v3', 'v4-addendum', 'v5-program-guard'],
                        help='Freeze a dataset explicitly; default is v3. Run uses the frozen copy.')
    parser.add_argument('--allow-paid', action='store_true')
    parser.add_argument('--resume', action='store_true', help='Continue unclaimed samples only; never repeat an existing claim')
    args = parser.parse_args(argv)
    if args.action == 'freeze':
        selected = {'v3': DATASET, 'v4-addendum': ADDENDUM_DATASET,
                    'v5-program-guard': PROGRAM_GUARD_DATASET}
        freeze(args.root, selected[args.dataset or 'v3'])
    else:
        if args.dataset is not None:
            parser.error('--dataset selects input at freeze time only')
        if not args.split:
            parser.error('--split is required')
        results = run(args.root, args.split, allow_paid=args.allow_paid, resume=args.resume)
        if any(item['error'] or any(not row['match'] for row in item['checks']) for item in results):
            raise SystemExit(1)


if __name__ == '__main__':
    main()
