"""Read-only evidence checks around an explicitly clicked isolated browser apply."""
import argparse
import json
from pathlib import Path
from urllib.request import urlopen

from tests.skill_calibration_fixture import tree_hashes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8898)
    parser.add_argument('--phase', choices=['before', 'after'], required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    from creatoros.config import PROJECT_ROOT
    if not root.is_relative_to((PROJECT_ROOT / 'tmp').resolve()):
        raise ValueError('Only isolated tmp fixtures may be verified')
    with urlopen(f'http://127.0.0.1:{args.port}/api/calibration-probe/evidence', timeout=30) as response:
        evidence = json.load(response)
    setup = evidence['setup']
    assert Path(setup['target_path']).resolve().is_relative_to(root)
    assert evidence['artifact_files'] == setup['artifact_files'], 'Old artifact bytes changed'
    assert evidence['companion_files'] == setup['companion_files'], 'Shared companion changed'
    assert set(evidence['counts'].values()) == {2}, evidence['counts']
    assert len(evidence['calibrations']) == 1
    calibration = evidence['calibrations'][0]
    assert len(calibration['trials']) == 1, 'Unexpected paid re-submission'
    trial = calibration['trials'][0]
    assert trial['status'] == 'completed' and len(trial['cards']) == 1
    assert trial['thread_id'] != calibration['thread_id'], 'Trial reused the draft thread'
    assert trial['digest'] == calibration['draft_digest'] and trial['artifact_digest']
    if args.phase == 'before':
        assert calibration['status'] == 'draft'
        assert evidence['target_files'] == setup['baseline_files'], 'Shared target changed before confirmation'
    else:
        assert calibration['status'] == 'applied'
        assert evidence['target_digest'] == calibration['applied_digest']
        assert evidence['target_files'] != setup['baseline_files']
        backup = root / 'skills' / 'calibration-applications' / calibration['id'] / calibration['apply_receipt']['request_key'] / 'backup'
        assert tree_hashes(backup) == setup['baseline_files'], 'Old version backup missing or changed'
    report = root / f'verified-{args.phase}.json'
    report.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(phase=args.phase, success=True, trial_cards=len(trial['cards']),
        clean_thread=True, original_artifacts_unchanged=True, companion_unchanged=True,
        counts=evidence['counts'], report=str(report)), ensure_ascii=False))


if __name__ == '__main__':
    main()
