from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn
from filelock import FileLock, Timeout
from sqlalchemy.engine import make_url

from creatoros.config import DATABASE_URL, PROJECT_ROOT
from creatoros.storage import upgrade_database
from creatoros.runs import ContentRunService
from creatoros.storage import Database
from creatoros.maintenance.state import Layout, safe_path
from creatoros.maintenance.control import MaintenanceController, MaintenanceMiddleware

from .app import create_app
from .launcher import StudioLaunchError, ensure_studio_build
from .server import StudioServer


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the local CreatorOS Studio.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--database-url", default=DATABASE_URL)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--maintenance-start", action="store_true",
                        help="Start with business consumers and recovery deferred.")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    token = os.environ.pop("CREATOROS_MAINTENANCE_TOKEN", None)
    if args.maintenance_start and (not token or len(token) < 32):
        parser.error("--maintenance-start requires the private maintenance environment token")
    url = make_url(args.database_url)
    if url.get_backend_name() != "sqlite" or url.database in {None, "", ":memory:"}:
        parser.error("Managed Studio requires a persistent SQLite database")
    layout = Layout.from_paths(Path(url.database), args.output_root or PROJECT_ROOT / "outputs")
    layout.control_dir.mkdir(parents=True, exist_ok=True)
    owner = FileLock(str(safe_path(layout.control_dir / "owner.lock")), timeout=0, thread_local=False)
    controller, database = None, None
    try:
        # Refuse another registered host before migrations can touch its data.
        owner.acquire()
        # Legacy Web/CLI hosts do not own the maintenance lock, but an active
        # producer owns this original execution lock. Never migrate underneath it.
        migration_guard = FileLock(str(safe_path(layout.database.with_name(layout.database.name + ".execution.lock"))),
                                   timeout=0, thread_local=False)
        with migration_guard:
            upgrade_database(args.database_url)
        dist = ensure_studio_build(PROJECT_ROOT)
        database = Database(args.database_url)
        runs = ContentRunService(database, output_root=layout.output)
        app = create_app(database=database, run_service=runs, studio_dist=dist,
                         defer_background_start=args.maintenance_start)
        config = uvicorn.Config(app, host="127.0.0.1", port=args.port, timeout_graceful_shutdown=3)
        server = StudioServer(config)
        controller = MaintenanceController(app, layout, port=args.port, token=token,
            held=args.maintenance_start, shutdown_callback=lambda: setattr(server, "should_exit", True))
        app.state.maintenance = controller
        app.add_middleware(MaintenanceMiddleware, controller=controller)
        controller.register(owner=owner)
        print(f"CreatorOS Studio 已就绪：http://127.0.0.1:{args.port}", flush=True)
        server.run()
    except Timeout:
        parser.exit(2, "CreatorOS Studio 无法启动：已有实例占用此数据库，未迁移或接管。\n")
    except StudioLaunchError as error:
        parser.exit(2, f"CreatorOS Studio 无法启动：{error}\n")
    except KeyboardInterrupt:
        pass
    finally:
        try:
            if database is not None:
                database.close()
        finally:
            try:
                if controller is not None:
                    controller.close()
            finally:
                owner.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
