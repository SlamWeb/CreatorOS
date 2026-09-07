"""Isolated real-DeepSeek browser server; seeded cancelled Run prevents image spending."""
import argparse
from datetime import datetime
from pathlib import Path

import uvicorn

from creatoros.config import PROJECT_ROOT
from creatoros.web.server import StudioServer
from creatoros.web.app import create_app
from creatoros.storage import Database
from creatoros.runs import ContentRunService
from tests.studio_review_fixtures import make_fixture


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8884)
    parser.add_argument("--review-root", type=Path)
    args = parser.parse_args()
    root = args.review_root or PROJECT_ROOT / "tmp" / f"web-agent-live-{datetime.now():%Y%m%d-%H%M%S}"
    producer = None
    if args.review_root:
        db = Database(f"sqlite:///{(root / 'review.db').as_posix()}")
        runs = ContentRunService(db, output_root=root / "outputs")
        app = create_app(database=db, run_service=runs)
    else:
        root.mkdir(parents=True)
        db, runs, producer, app = make_fixture(root)
        run = runs.create("review-1")
        runs.cancel(run.id, expected_version=run.version)
    print(f"isolated_root={root}", flush=True)
    try:
        StudioServer(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning",
                                   timeout_graceful_shutdown=3)).run()
    finally:
        assert producer is None or producer.calls == 0, "This probe must not generate images"
        db.close()


if __name__ == "__main__":
    main()
