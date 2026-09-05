from __future__ import annotations

import argparse

import uvicorn

from creatoros.config import DATABASE_URL, PROJECT_ROOT
from creatoros.storage import upgrade_database

from .app import create_app
from .launcher import StudioLaunchError, ensure_studio_build
from .server import StudioServer


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the local CreatorOS Studio.")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        dist = ensure_studio_build(PROJECT_ROOT)
    except StudioLaunchError as error:
        parser.exit(2, f"CreatorOS Studio 无法启动：{error}\n")
    upgrade_database(DATABASE_URL)
    app = create_app(DATABASE_URL, studio_dist=dist)
    print(f"CreatorOS Studio 已就绪：http://127.0.0.1:{args.port}", flush=True)
    config = uvicorn.Config(app, host="127.0.0.1", port=args.port, timeout_graceful_shutdown=3)
    try:
        StudioServer(config).run()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
