"""Opt-in real Codex calibration/trial in a new isolated root; no publication."""
import argparse
import json
from pathlib import Path

import uvicorn
from fastapi.encoders import jsonable_encoder

from creatoros.config import PROJECT_ROOT
from creatoros.integrations.producer_skills import ProducerSkillCatalog, SkillInstallService
from creatoros.integrations.skill_extraction import SkillExtractionService
from creatoros.integrations.topic_research import TopicResearchService
from creatoros.runs import ContentRunService
from creatoros.storage import Database, upgrade_database
from creatoros.web.app import create_app
from creatoros.web.server import StudioServer
from tests.skill_calibration_fixture import calibration_evidence, seed_calibration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8898)
    parser.add_argument('--resume', action='store_true', help='Read the existing isolated setup; never re-seed or resubmit')
    parser.add_argument('--research', action='store_true', help='Explicitly enable real Codex research in the isolated account')
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_relative_to((PROJECT_ROOT / 'tmp').resolve()) or args.reference.suffix.lower() != '.png':
        raise ValueError('Use a new ignored tmp root and an explicit PNG reference')
    if args.resume and not (root / 'setup.json').is_file():
        raise ValueError('Resume requires the original isolated setup.json')
    root.mkdir(parents=True, exist_ok=args.resume)
    url = f"sqlite:///{(root / 'calibration.db').as_posix()}"
    upgrade_database(url)
    db = Database(url)

    def denied(*args, **kwargs):
        raise AssertionError('Only explicit draft calibration/trial is authorized in this probe')

    class Forbidden:
        research = denied
        preflight = denied
        __getattr__ = lambda self, name: denied

    catalog = ProducerSkillCatalog(root / 'skills')
    extractions = SkillExtractionService(catalog)
    runs = ContentRunService(db, output_root=root / 'outputs', producer_factory=denied)
    app = create_app(database=db, run_service=runs, chat_root=root / 'chats',
        skill_install_service=SkillInstallService(catalog, installer=Forbidden()),
        skill_extraction_service=extractions,
        topic_research_service=TopicResearchService(db, catalog, researcher=None if args.research else Forbidden()),
        publication_client=Forbidden(), eval_root=root / 'eval')
    if args.resume:
        setup = json.loads((root / 'setup.json').read_text(encoding='utf-8'))
        app.state.calibration_fixture = setup
    else:
        setup = seed_calibration(app, root, live=True, reference_path=args.reference)
        (root / 'setup.json').write_text(json.dumps(setup, ensure_ascii=False, indent=2), encoding='utf-8')

    @app.get('/api/calibration-probe/evidence')
    def evidence():
        return calibration_evidence(app)

    app.router.routes.insert(0, app.router.routes.pop())
    print(f'isolated_root={root}', flush=True)
    try:
        StudioServer(uvicorn.Config(app, host='127.0.0.1', port=args.port,
            log_level='warning', timeout_graceful_shutdown=3)).run()
    finally:
        (root / 'final-evidence.json').write_text(json.dumps(jsonable_encoder(calibration_evidence(app)),
            ensure_ascii=False, indent=2), encoding='utf-8')
        db.close()


if __name__ == '__main__':
    main()
