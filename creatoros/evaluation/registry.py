"""Transactional, local shared registry for formal evaluation records.

The registry never starts an evaluation. Producers register a plan before doing
work and append results afterwards. The database is separate from application
and historical benchmark data, and can be shared by independent worktrees.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sqlite3

from creatoros.integrations.codex_public_events import sanitize_public


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$")
_RESULT_STATUS = {"passed", "failed", "needs_review", "not_run", "unknown"}
_HEARTBEAT_TIMEOUT = timedelta(seconds=60)
_TRANSITIONS = {
    "registered": {"freezing", "failed", "interrupted", "unknown"},
    "freezing": {"running", "failed", "interrupted", "unknown"},
    "running": {"completed", "failed", "interrupted", "unknown"},
    "completed": set(), "failed": set(), "interrupted": set(), "unknown": set(),
}
_SECRET_KEYS = {"api_key", "apikey", "authorization", "password", "secret", "access_token", "refresh_token", "credential"}


class RegistryError(ValueError):
    def __init__(self, message: str, code: str = "invalid", status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


def _id(value, name):
    if not isinstance(value, str) or not _ID.fullmatch(value) or value in {".", ".."}:
        raise RegistryError(f"Invalid {name}")
    return value


def _time():
    return datetime.now(timezone.utc).isoformat()


def _json(value):
    def check(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str) or key.lower() in _SECRET_KEYS:
                    raise RegistryError("Sensitive or invalid metadata key")
                check(child)
        elif isinstance(item, list):
            for child in item:
                check(child)
    check(value)
    _, redacted, omissions = sanitize_public(value)
    if redacted or omissions:
        raise RegistryError("Metadata must be sanitized before registration")
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise RegistryError("Invalid JSON metadata") from error
    if len(encoded.encode("utf-8")) > 1024 * 1024:
        raise RegistryError("Evaluation record too large")
    return encoded


def _object(value, name):
    if not isinstance(value, dict):
        raise RegistryError(f"{name} must be an object")
    return value


class EvalRegistry:
    def __init__(self, path=None):
        configured = path or os.environ.get("CREATOROS_EVAL_REGISTRY")
        self.path = Path(configured).expanduser().absolute() if configured else Path.home() / ".creatoros" / "eval" / "registry.sqlite3"

    @contextmanager
    def _connection(self, write=False):
        if not write and not self.path.exists():
            yield None
            return
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
                tables = {row["name"] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                if tables and "eval_registry_meta" not in tables:
                    raise RegistryError("Configured database is not an evaluation registry", "invalid_registry", 409)
                if "eval_registry_meta" in tables:
                    marker = connection.execute("SELECT value FROM eval_registry_meta WHERE key='identity'").fetchone()
                    if not marker or marker[0] != "creatoros-eval-registry-v1":
                        raise RegistryError("Configured database has an unknown registry identity", "invalid_registry", 409)
                schema = """
                CREATE TABLE IF NOT EXISTS eval_registry_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS eval_rounds (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS eval_runs (
                    id TEXT PRIMARY KEY, round_id TEXT NOT NULL REFERENCES eval_rounds(id),
                    payload TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
                    started_at TEXT, finished_at TEXT, error TEXT
                );
                CREATE TABLE IF NOT EXISTS eval_round_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, round_id TEXT NOT NULL REFERENCES eval_rounds(id),
                    merge_status TEXT NOT NULL, happened_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS eval_heartbeats (
                    run_id TEXT PRIMARY KEY REFERENCES eval_runs(id), last_seen_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS eval_attempts (
                    run_id TEXT NOT NULL REFERENCES eval_runs(id), case_id TEXT NOT NULL,
                    round_attempt INTEGER NOT NULL, PRIMARY KEY(run_id, case_id)
                );
                CREATE TABLE IF NOT EXISTS eval_manifests (
                    run_id TEXT PRIMARY KEY REFERENCES eval_runs(id), manifest_sha256 TEXT NOT NULL,
                    bound_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS eval_results (
                    run_id TEXT NOT NULL REFERENCES eval_runs(id), case_id TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(run_id, case_id)
                );
                CREATE TABLE IF NOT EXISTS eval_recovery_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES eval_runs(id),
                    case_id TEXT NOT NULL, source_batch TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,
                    record_sha256 TEXT NOT NULL, happened_at TEXT NOT NULL,
                    UNIQUE(run_id, case_id)
                );
                CREATE TABLE IF NOT EXISTS eval_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES eval_runs(id),
                    status TEXT NOT NULL, happened_at TEXT NOT NULL, error TEXT
                );
                """
                for statement in schema.split(";"):
                    if statement.strip():
                        connection.execute(statement)
                connection.execute("INSERT OR IGNORE INTO eval_registry_meta(key,value) VALUES (?,?)",
                                   ("identity", "creatoros-eval-registry-v1"))
            yield connection
            if write:
                connection.commit()
        except Exception:
            if write:
                connection.rollback()
            raise
        finally:
            connection.close()

    def register_round(self, data):
        data = _object(data, "round")
        allowed = {"id", "title", "change_summary", "code_version", "branch", "merge_status", "parent_round_id", "changes"}
        if set(data) - allowed:
            raise RegistryError("Unsupported round fields")
        _id(data.get("id"), "round id")
        if not isinstance(data.get("title"), str) or not data["title"].strip():
            raise RegistryError("Round title required")
        if not isinstance(data.get("code_version"), str) or not data["code_version"].strip():
            raise RegistryError("Code version required")
        if data.get("parent_round_id") is not None:
            _id(data["parent_round_id"], "parent round id")
        payload = {"id": data["id"], "title": data["title"], "change_summary": data.get("change_summary", ""),
                   "code_version": data["code_version"], "branch": data.get("branch"),
                   "merge_status": data.get("merge_status", "unknown"), "parent_round_id": data.get("parent_round_id"),
                   "changes": data.get("changes", [])}
        if payload["merge_status"] not in {"developing", "merged", "unknown"} or not isinstance(payload["changes"], list):
            raise RegistryError("Invalid round metadata")
        encoded = _json(payload)
        with self._connection(write=True) as db:
            row = db.execute("SELECT * FROM eval_rounds WHERE id=?", (payload["id"],)).fetchone()
            if row:
                if row["payload"] != encoded:
                    raise RegistryError("Round ID already has different content", "conflict", 409)
                return self._round(db, row)
            if payload["parent_round_id"] and not db.execute("SELECT 1 FROM eval_rounds WHERE id=?", (payload["parent_round_id"],)).fetchone():
                raise RegistryError("Parent round not found", "not_found", 404)
            created = _time()
            db.execute("INSERT INTO eval_rounds VALUES (?,?,?)", (payload["id"], encoded, created))
            return self._round(db, db.execute("SELECT * FROM eval_rounds WHERE id=?", (payload["id"],)).fetchone())

    def set_merge_status(self, round_id, status):
        _id(round_id, "round id")
        if status not in {"developing", "merged", "unknown"}:
            raise RegistryError("Invalid merge status")
        with self._connection(write=True) as db:
            row = db.execute("SELECT * FROM eval_rounds WHERE id=?", (round_id,)).fetchone()
            if not row:
                raise RegistryError("Round not found", "not_found", 404)
            current = self._round(db, row)["merge_status"]
            if current == status:
                return self._round(db, row)
            if current == "merged":
                raise RegistryError("Merged status cannot be reversed", "conflict", 409)
            db.execute("INSERT INTO eval_round_events(round_id,merge_status,happened_at) VALUES (?,?,?)",
                       (round_id, status, _time()))
            return self._round(db, row)

    def register_run(self, data):
        data = _object(data, "run")
        allowed = {"id", "round_id", "suite_id", "suite_version", "planned_cases", "metadata"}
        if set(data) - allowed:
            raise RegistryError("Unsupported run fields")
        for name in ("id", "round_id", "suite_id", "suite_version"):
            _id(data.get(name), name)
        cases = data.get("planned_cases")
        if not isinstance(cases, list) or not cases or any(not isinstance(case, str) for case in cases) or len(cases) != len(set(cases)):
            raise RegistryError("Planned cases must be a nonempty unique list")
        for case_id in cases:
            _id(case_id, "case id")
        metadata = _object(data.get("metadata", {}), "metadata")
        if "requires_manifest_binding" in metadata and not isinstance(metadata["requires_manifest_binding"], bool):
            raise RegistryError("requires_manifest_binding must be boolean")
        payload = {"id": data["id"], "round_id": data["round_id"], "suite_id": data["suite_id"],
                   "suite_version": data["suite_version"], "planned_cases": cases, "metadata": metadata}
        encoded = _json(payload)
        with self._connection(write=True) as db:
            row = db.execute("SELECT * FROM eval_runs WHERE id=?", (data["id"],)).fetchone()
            if row:
                if row["payload"] != encoded:
                    raise RegistryError("Run ID already has different content", "conflict", 409)
                return self._run(db, row)
            if not db.execute("SELECT 1 FROM eval_rounds WHERE id=?", (data["round_id"],)).fetchone():
                raise RegistryError("Round not found", "not_found", 404)
            previous = [json.loads(item["payload"]) for item in db.execute(
                "SELECT payload FROM eval_runs WHERE round_id=?", (data["round_id"],))]
            attempts = {case_id: 1 + sum(
                1 for item in previous if item.get("suite_id") == data["suite_id"]
                and case_id in item.get("planned_cases", [])) for case_id in cases}
            created = _time()
            db.execute("INSERT INTO eval_runs(id,round_id,payload,status,created_at) VALUES (?,?,?,?,?)",
                       (data["id"], data["round_id"], encoded, "registered", created))
            db.executemany("INSERT INTO eval_attempts(run_id,case_id,round_attempt) VALUES (?,?,?)",
                           [(data["id"], case_id, attempts[case_id]) for case_id in cases])
            db.execute("INSERT INTO eval_events(run_id,status,happened_at) VALUES (?,?,?)", (data["id"], "registered", created))
            return self._run(db, db.execute("SELECT * FROM eval_runs WHERE id=?", (data["id"],)).fetchone())

    def claim_execution(self, run_id):
        """Atomically claim an unstarted registration; duplicate claims conflict."""
        _id(run_id, "run id")
        with self._connection(write=True) as db:
            row = db.execute("SELECT * FROM eval_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise RegistryError("Run not found", "not_found", 404)
            if row["status"] != "registered":
                raise RegistryError("Run has already been claimed", "conflict", 409)
            now = _time()
            db.execute("UPDATE eval_runs SET status='freezing' WHERE id=?", (run_id,))
            db.execute("INSERT INTO eval_events(run_id,status,happened_at) VALUES (?,?,?)", (run_id, "freezing", now))
            return self._run(db, db.execute("SELECT * FROM eval_runs WHERE id=?", (run_id,)).fetchone())

    def transition(self, run_id, status, error=None):
        _id(run_id, "run id")
        if status not in _TRANSITIONS:
            raise RegistryError("Invalid run status")
        if error is not None and (not isinstance(error, str) or len(error) > 2000):
            raise RegistryError("Invalid error")
        _json({"error": error})
        with self._connection(write=True) as db:
            row = db.execute("SELECT * FROM eval_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise RegistryError("Run not found", "not_found", 404)
            if row["status"] == status:
                if row["error"] != error:
                    raise RegistryError("Status already recorded with different content", "conflict", 409)
                return self._run(db, row)
            if status not in _TRANSITIONS[row["status"]]:
                raise RegistryError("Invalid status transition", "conflict", 409)
            if status == "running" and json.loads(row["payload"]).get("metadata", {}).get("requires_manifest_binding") is True:
                if not db.execute("SELECT 1 FROM eval_manifests WHERE run_id=?", (run_id,)).fetchone():
                    raise RegistryError("Frozen manifest must be bound before execution", "conflict", 409)
            now = _time()
            started = now if status == "running" else row["started_at"]
            finished = now if status in {"completed", "failed", "interrupted", "unknown"} else None
            db.execute("UPDATE eval_runs SET status=?,started_at=?,finished_at=?,error=? WHERE id=?",
                       (status, started, finished, error, run_id))
            db.execute("INSERT INTO eval_events(run_id,status,happened_at,error) VALUES (?,?,?,?)", (run_id, status, now, error))
            return self._run(db, db.execute("SELECT * FROM eval_runs WHERE id=?", (run_id,)).fetchone())

    def bind_manifest(self, run_id, manifest_sha256):
        """Bind one immutable frozen manifest to a claimed, freezing run."""
        _id(run_id, "run id")
        if not isinstance(manifest_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256):
            raise RegistryError("Invalid manifest SHA-256")
        with self._connection(write=True) as db:
            row = db.execute("SELECT status FROM eval_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise RegistryError("Run not found", "not_found", 404)
            if row["status"] != "freezing":
                raise RegistryError("Manifest can only be bound while freezing", "conflict", 409)
            existing = db.execute("SELECT manifest_sha256 FROM eval_manifests WHERE run_id=?", (run_id,)).fetchone()
            if existing:
                if existing["manifest_sha256"] != manifest_sha256:
                    raise RegistryError("Frozen manifest already bound to different content", "conflict", 409)
                return {"run_id": run_id, "manifest_sha256": manifest_sha256}
            db.execute("INSERT INTO eval_manifests(run_id,manifest_sha256,bound_at) VALUES (?,?,?)",
                       (run_id, manifest_sha256, _time()))
            return {"run_id": run_id, "manifest_sha256": manifest_sha256}

    def _case_payload(self, data):
        data = _object(data, "result")
        allowed = {"case_id", "status", "summary", "metrics", "evidence", "case_definition_hash"}
        if set(data) - allowed:
            raise RegistryError("Unsupported result fields")
        case_id = _id(data.get("case_id"), "case id")
        if data.get("status") not in _RESULT_STATUS:
            raise RegistryError("Invalid case status")
        payload = {"case_id": case_id, "status": data["status"], "summary": data.get("summary", ""),
                   "metrics": _object(data.get("metrics", {}), "metrics"),
                   "evidence": _object(data.get("evidence", {}), "evidence"),
                   "case_definition_hash": data.get("case_definition_hash")}
        return payload, _json(payload)

    def add_case_result(self, run_id, data):
        _id(run_id, "run id")
        payload, encoded = self._case_payload(data)
        case_id = payload["case_id"]
        with self._connection(write=True) as db:
            row = db.execute("SELECT * FROM eval_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise RegistryError("Run not found", "not_found", 404)
            if case_id not in json.loads(row["payload"])["planned_cases"]:
                raise RegistryError("Case was not planned", "conflict", 409)
            existing = db.execute("SELECT payload FROM eval_results WHERE run_id=? AND case_id=?", (run_id, case_id)).fetchone()
            if existing:
                if existing["payload"] != encoded:
                    raise RegistryError("Case result already exists with different content", "conflict", 409)
                return payload
            if row["status"] != "running":
                raise RegistryError("Run is not running", "conflict", 409)
            db.execute("INSERT INTO eval_results VALUES (?,?,?,?)", (run_id, case_id, encoded, _time()))
            return payload

    def recover_case_result(self, run_id, data, *, source_batch, manifest_sha256, record_sha256):
        """Append verified on-disk results without reopening or rerunning a terminal run.

        This local API validates identifiers and immutable provenance claims only;
        its trusted caller must verify the manifest, record, and their binding.
        """
        _id(run_id, "run id")
        _id(source_batch, "source batch")
        if source_batch != run_id:
            raise RegistryError("Recovery source batch does not match run", "conflict", 409)
        if any(not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
               for digest in (manifest_sha256, record_sha256)):
            raise RegistryError("Recovery requires valid manifest and record SHA-256 digests")
        payload, encoded = self._case_payload(data)
        case_id = payload["case_id"]
        with self._connection(write=True) as db:
            row = db.execute("SELECT * FROM eval_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise RegistryError("Run not found", "not_found", 404)
            if row["status"] not in {"failed", "interrupted", "unknown"}:
                raise RegistryError("Recovery requires an interrupted or unknown terminal run", "conflict", 409)
            bound = db.execute("SELECT manifest_sha256 FROM eval_manifests WHERE run_id=?", (run_id,)).fetchone()
            if not bound or bound["manifest_sha256"] != manifest_sha256:
                raise RegistryError("Recovery manifest is not the manifest bound to this run", "conflict", 409)
            if case_id not in json.loads(row["payload"])["planned_cases"]:
                raise RegistryError("Case was not planned", "conflict", 409)
            existing = db.execute("SELECT payload FROM eval_results WHERE run_id=? AND case_id=?", (run_id, case_id)).fetchone()
            if existing:
                if existing["payload"] != encoded:
                    raise RegistryError("Case result already exists with different content", "conflict", 409)
                recovery = db.execute("SELECT source_batch,manifest_sha256,record_sha256 FROM eval_recovery_events "
                                      "WHERE run_id=? AND case_id=?", (run_id, case_id)).fetchone()
                if recovery and tuple(recovery) != (source_batch, manifest_sha256, record_sha256):
                    raise RegistryError("Recovery provenance conflicts", "conflict", 409)
                return payload
            now = _time()
            db.execute("INSERT INTO eval_results VALUES (?,?,?,?)", (run_id, case_id, encoded, now))
            db.execute("INSERT INTO eval_recovery_events(run_id,case_id,source_batch,manifest_sha256,record_sha256,happened_at) "
                       "VALUES (?,?,?,?,?,?)", (run_id, case_id, source_batch, manifest_sha256, record_sha256, now))
            return payload

    def heartbeat(self, run_id):
        """Refresh producer liveness; a heartbeat never changes execution state."""
        _id(run_id, "run id")
        with self._connection(write=True) as db:
            row = db.execute("SELECT status FROM eval_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise RegistryError("Run not found", "not_found", 404)
            if row["status"] not in {"registered", "freezing", "running"}:
                raise RegistryError("Run is terminal", "conflict", 409)
            now = _time()
            db.execute("INSERT INTO eval_heartbeats(run_id,last_seen_at) VALUES (?,?) "
                       "ON CONFLICT(run_id) DO UPDATE SET last_seen_at=excluded.last_seen_at", (run_id, now))
            return {"run_id": run_id, "last_seen_at": now}

    def _run(self, db, row):
        value = json.loads(row["payload"])
        value.update({key: row[key] for key in ("status", "created_at", "started_at", "finished_at", "error")})
        has_manifests = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='eval_manifests'").fetchone()
        manifest = db.execute("SELECT manifest_sha256 FROM eval_manifests WHERE run_id=?", (row["id"],)).fetchone() if has_manifests else None
        value["manifest_sha256"] = manifest["manifest_sha256"] if manifest else None
        heartbeat = db.execute("SELECT last_seen_at FROM eval_heartbeats WHERE run_id=?", (row["id"],)).fetchone()
        value["heartbeat_at"] = heartbeat["last_seen_at"] if heartbeat else None
        value["liveness"] = "not_applicable"
        value["display_status"] = row["status"]
        if row["status"] in {"freezing", "running"}:
            value["liveness"] = "unknown"
            if heartbeat:
                try:
                    last_seen = datetime.fromisoformat(heartbeat["last_seen_at"])
                    age = datetime.now(timezone.utc) - last_seen
                    if timedelta(0) <= age <= _HEARTBEAT_TIMEOUT:
                        value["liveness"] = "live"
                except (TypeError, ValueError, OverflowError):
                    pass
            if value["liveness"] == "unknown":
                value["display_status"] = "unknown"
        has_attempts = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='eval_attempts'").fetchone()
        attempts = {item["case_id"]: item["round_attempt"] for item in db.execute(
            "SELECT case_id,round_attempt FROM eval_attempts WHERE run_id=?", (row["id"],))} if has_attempts else {}
        value["case_attempts"] = {case_id: attempts.get(case_id) for case_id in value["planned_cases"]}
        value["results"] = []
        for result in db.execute("SELECT payload FROM eval_results WHERE run_id=? ORDER BY created_at,case_id", (row["id"],)):
            result_data = json.loads(result["payload"])
            result_data["round_attempt"] = attempts.get(result_data["case_id"])
            value["results"].append(result_data)
        value["events"] = [dict(event) for event in db.execute(
            "SELECT status,happened_at,error FROM eval_events WHERE run_id=? ORDER BY seq", (row["id"],))]
        has_recoveries = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='eval_recovery_events'").fetchone()
        value["recoveries"] = [dict(event) for event in db.execute(
            "SELECT case_id,source_batch,manifest_sha256,record_sha256,happened_at "
            "FROM eval_recovery_events WHERE run_id=? ORDER BY seq", (row["id"],))] if has_recoveries else []
        return value

    def _round(self, db, row):
        value = json.loads(row["payload"])
        value["created_at"] = row["created_at"]
        events = [dict(event) for event in db.execute(
            "SELECT merge_status,happened_at FROM eval_round_events WHERE round_id=? ORDER BY seq", (row["id"],))]
        if events:
            value["merge_status"] = events[-1]["merge_status"]
        value["merge_events"] = events
        return value

    def rounds(self):
        with self._connection() as db:
            if db is None:
                return {"items": []}
            return {"items": [self._round(db, row) for row in db.execute(
                "SELECT * FROM eval_rounds ORDER BY created_at DESC,id DESC")]}

    def round_detail(self, round_id):
        _id(round_id, "round id")
        with self._connection() as db:
            row = db.execute("SELECT * FROM eval_rounds WHERE id=?", (round_id,)).fetchone() if db else None
            if not row:
                raise RegistryError("Round not found", "not_found", 404)
            round_data = self._round(db, row)
            runs = [self._run(db, item) for item in db.execute(
                "SELECT * FROM eval_runs WHERE round_id=? ORDER BY created_at DESC,id DESC", (round_id,))]
            return {"round": round_data, "runs": runs}

    def get_run(self, run_id):
        _id(run_id, "run id")
        with self._connection() as db:
            row = db.execute("SELECT * FROM eval_runs WHERE id=?", (run_id,)).fetchone() if db else None
            if not row:
                raise RegistryError("Run not found", "not_found", 404)
            return self._run(db, row)
