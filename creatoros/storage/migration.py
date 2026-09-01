from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url


def _ensure_sqlite_parent(database_url: str) -> None:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return
    Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def upgrade_database(database_url: str, revision: str = "head") -> None:
    from ..config import PROJECT_ROOT

    _ensure_sqlite_parent(database_url)
    config = Config(str(Path(PROJECT_ROOT) / "alembic.ini"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, revision)
