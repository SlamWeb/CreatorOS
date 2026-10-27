"""Read-only SQLite backup -> migrate COPY -> compare every original column/row."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3


def snapshot(connection, schema=None):
    tables = schema or {name: [row[1] for row in connection.execute(f'PRAGMA table_info("{name}")')]
        for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='alembic_version'")}
    result = {}
    for name, columns in tables.items():
        quoted = ",".join('"' + column.replace('"', '""') + '"' for column in columns)
        rows = sorted(json.dumps(tuple(row), ensure_ascii=False, default=str) for row in connection.execute(f'SELECT {quoted} FROM "{name}"'))
        result[name] = dict(count=len(rows), sha256=hashlib.sha256("\n".join(rows).encode()).hexdigest())
    return tables, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    source, target = args.source.resolve(strict=True), args.target.resolve()
    if target.exists() or not target.is_relative_to(Path(__file__).resolve().parents[1] / "tmp"):
        parser.error("Target must be a new SQLite file under tmp")
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as read, sqlite3.connect(target) as write:
        read.backup(write)
        schema, before = snapshot(write)
    from creatoros.storage import upgrade_database
    upgrade_database("sqlite:///" + target.as_posix())
    with sqlite3.connect(target) as result:
        _, after = snapshot(result, schema)
        integrity = result.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = result.execute("PRAGMA foreign_key_check").fetchall()
    report = dict(unchanged=before == after, tables=before, integrity=integrity, foreign_key_errors=len(foreign_keys))
    target.with_suffix(".verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(dict(unchanged=report["unchanged"], table_count=len(before),
        row_count=sum(item["count"] for item in before.values()), integrity=integrity,
        foreign_key_errors=len(foreign_keys))))
    if before != after or integrity != "ok" or foreign_keys:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
