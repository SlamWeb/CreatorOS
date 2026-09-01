from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker


def _enable_sqlite_guards(dbapi_connection, _connection_record) -> None:
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


class Database:
    def __init__(self, database_url: str):
        if not database_url.strip():
            raise ValueError("database_url 不能为空。")
        url = make_url(database_url)
        connect_args = {"check_same_thread": False} if url.get_backend_name() == "sqlite" else {}
        self.engine: Engine = create_engine(
            url,
            connect_args=connect_args,
            pool_pre_ping=True,
        )
        if url.get_backend_name() == "sqlite":
            event.listen(self.engine, "connect", _enable_sqlite_guards)
        self._session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
        )

    @classmethod
    def from_defaults(cls) -> "Database":
        from ..config import DATABASE_URL

        return cls(DATABASE_URL)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def close(self) -> None:
        self.engine.dispose()
