"""Read-only inventory and preservation evidence, independent of a running app."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import stat


class MaintenanceError(RuntimeError):
    pass


def safe_path(path: Path) -> Path:
    path = Path(path).absolute()
    for part in (path, *path.parents):
        try:
            attributes = getattr(part.lstat(), "st_file_attributes", 0)
        except FileNotFoundError:
            attributes = 0
        if part.is_symlink() or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise MaintenanceError("受管路径含链接，不能自动维护：" + str(part))
    return path.resolve()


@dataclass(frozen=True)
class Layout:
    database: Path
    output: Path
    skills: Path
    chats: Path

    @classmethod
    def from_paths(cls, database, output, *, chats=None, skills=None):
        db = safe_path(database)
        default_skills = db.parent / ("producer-skills" if db.name == "creatoros.db" else db.stem + "-producer-skills")
        layout = cls(db, safe_path(output), safe_path(skills or default_skills),
                     safe_path(chats or db.parent / (db.stem + "-agent-sessions")))
        # Evidence cannot live inside any content tree that is snapshotted.
        roots = list(layout.roots().values())
        if any(layout.control_dir.is_relative_to(root) or root.is_relative_to(layout.control_dir) for root in roots):
            raise MaintenanceError("维护目录与业务文件根目录不能互相包含。")
        return layout

    @property
    def discussions(self):
        return self.chats.with_name(self.chats.name + "-discussions")

    @property
    def research(self):
        return self.skills.with_name(self.skills.name + "-topic-research")

    @property
    def references(self):
        return self.database.parent / (self.database.stem + "-topic-references")

    @property
    def control_dir(self):
        return safe_path(self.database.with_name(self.database.name + ".maintenance"))

    @property
    def instance_file(self):
        return self.control_dir / "instance.json"

    def roots(self):
        return dict(skills=self.skills, output=self.output, chats=self.chats,
                    discussions=self.discussions, research=self.research, references=self.references)

    def fingerprint(self):
        return {"database": str(self.database), **{key: str(value) for key, value in self.roots().items()}}


@contextmanager
def connect(layout):
    path = safe_path(layout.database)
    if not path.is_file():
        raise MaintenanceError("数据库不存在，未创建新库。")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        yield connection
    finally:
        connection.close()


DB_BUSY = {
    "content_runs": {"producing", "validating"},
    "content_attempts": {"running"},
    "production_queue_items": {"pending", "running"},
    "topic_research_queue_items": {"queued", "researching"},
    "publication_requests": {"sending", "verifying"},
    "skill_calibrations": {"applying", "queued", "running", "producing"},
    "skill_iterations": {"queued", "running", "waiting_production"},
}
ACTIVE = {"queued", "running", "researching", "producing", "installing", "waiting_production"}
TERMINAL_TRIAL = {"completed", "failed", "interrupted", "cancelled"}
KNOWN = ACTIVE | TERMINAL_TRIAL | {"ready", "saved", "installed", "draft", "idle", "paused", "exhausted", "unknown", "cancel_requested", "stale"}
PATTERNS = {
    "skills": ("extractions/jobs/*/job.json", "jobs/*.json"),
    "research": ("batches/*.json",),
    "chats": ("*/view.json",),
    "discussions": ("records/*.json",),
}


def read_json(path):
    path = safe_path(path)
    if path.stat().st_size > 16 * 1024 * 1024:
        raise MaintenanceError("任务元数据超出维护读取上限。")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise MaintenanceError("任务元数据不是对象。")
    return value


def _metadata_busy(record):
    status = record.get("status")
    if not isinstance(status, str) or status not in KNOWN:
        return "任务状态缺失或未知"
    if status in ACTIVE or record.get("operation") or record.get("queue_state") in ACTIVE:
        return "任务执行或等待消费"
    queue = record.get("queue", {})
    if not isinstance(queue, dict):
        return "排队元数据损坏"
    if queue.get("state") in ACTIVE:
        return "任务已授权排队"
    if queue.get("state") not in {None, "completed", "failed", "interrupted", "cancelled"}:
        return "排队状态未知"
    for field in ("trials", "requests"):
        entries = record.get(field, [])
        if not isinstance(entries, list) or any(not isinstance(item, dict) for item in entries):
            return field + "元数据损坏"
        if any(item.get("status") in ACTIVE for item in entries):
            return field + "中仍有活动项"
    return None


def _rsi_busy(layout, state):
    if not isinstance(state, dict) or not isinstance(state.get("rounds", []), list):
        return "RSI状态无法核对"
    calls = state.get("calls", {})
    if not isinstance(calls, dict) or any(not isinstance(call, dict) for call in calls.values()):
        return "RSI请求回执损坏"
    if any(call.get("status") == "inflight" for call in calls.values()):
        return "RSI模型请求尚未确认结束"
    for round_ in state.get("rounds", []):
        if not isinstance(round_, dict):
            return "RSI轮次损坏"
        job_id, trial_id = round_.get("trial_job_id"), round_.get("trial_id")
        if not job_id:
            if round_.get("status") == "running":
                return "RSI缺少试产凭证"
            continue
        if not all(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) for value in (job_id, trial_id)):
            return "RSI试产身份无效"
        job = read_json(layout.skills / "extractions/jobs" / job_id / "job.json")
        matches = [item for item in job.get("trials", []) if isinstance(item, dict) and item.get("id") == trial_id]
        if job.get("id") != job_id or len(matches) != 1 or matches[0].get("status") not in TERMINAL_TRIAL:
            return "RSI试产仍活动或结果待核对"
    return None


def inspect_idle(layout):
    busy, scanned = [], 0
    try:
        journal = layout.database.with_name(layout.database.name + ".execution.json")
        if journal.exists() or journal.is_symlink():
            busy.append(dict(source="execution_journal", reason="旧执行者尚未核对，未清理记录"))
        with connect(layout) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table, statuses in DB_BUSY.items():
                if table not in tables:
                    busy.append(dict(source=table, reason="缺少应有表，不能确认兼容性"))
                    continue
                count = db.execute(f'SELECT count(*) FROM "{table}" WHERE status IN ({",".join("?" for _ in statuses)})',
                                   tuple(statuses)).fetchone()[0]
                if count:
                    busy.append(dict(source=table, reason="存在活动或已授权等待任务", count=count))
            if "skill_iterations" in tables:
                for identifier, raw in db.execute("SELECT id,state_json FROM skill_iterations"):
                    try:
                        issue = _rsi_busy(layout, json.loads(raw))
                        if issue:
                            busy.append(dict(source="skill_iterations/" + identifier, reason=issue))
                    except (OSError, ValueError, TypeError, MaintenanceError):
                        busy.append(dict(source="skill_iterations/" + identifier, reason="试产证据不可读，拒绝猜测"))
            if "agent_plans" in tables:
                count = db.execute("SELECT count(*) FROM agent_plans WHERE status='running' OR continuation_state IN ('pending','claimed')").fetchone()[0]
                if count:
                    busy.append(dict(source="agent_plans", reason="存在运行或等待续接的计划", count=count))
            if "agent_plan_steps" in tables:
                count = db.execute("SELECT count(*) FROM agent_plan_steps WHERE status IN ('running','waiting')").fetchone()[0]
                if count:
                    busy.append(dict(source="agent_plan_steps", reason="存在正在执行或等待结果的计划步骤", count=count))
            if "production_tasks" in tables:
                columns = {row[1] for row in db.execute("PRAGMA table_info(production_tasks)")}
                if "dispatch_state" in columns:
                    count = db.execute("SELECT count(*) FROM production_tasks WHERE dispatch_state='waiting'").fetchone()[0]
                    if count:
                        busy.append(dict(source="production_tasks", reason="存在等待前置图片的制作任务", count=count))
        for key, patterns in PATTERNS.items():
            root = safe_path(layout.roots()[key])
            for pattern in patterns:
                for path in sorted(root.glob(pattern)):
                    scanned += 1
                    try:
                        reason = _metadata_busy(read_json(path))
                        if reason:
                            busy.append(dict(source=key + "/" + path.relative_to(root).as_posix(), reason=reason))
                    except (OSError, ValueError, TypeError, MaintenanceError):
                        busy.append(dict(source=key + "/" + path.name, reason="元数据不可读，未忽略原文件"))
    except (OSError, ValueError, sqlite3.Error, MaintenanceError) as error:
        busy.append(dict(source="inventory", reason=str(error)))
    return dict(idle=not busy, busy=busy, metadata_scanned=scanned)


def _value(value):
    return {"bytes": base64.b64encode(value).decode()} if isinstance(value, bytes) else value


def _tables(layout, original=None):
    result = {}
    with connect(layout) as db:
        names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version' ORDER BY name")]
        for table in original if original is not None else names:
            if table not in names:
                raise MaintenanceError("原业务表丢失：" + table)
            quoted = '"' + table.replace('"', '""') + '"'
            columns = [row[1] for row in db.execute("PRAGMA table_info(" + quoted + ")")]
            wanted = original[table]["columns"] if original is not None else columns
            if not set(wanted) <= set(columns):
                raise MaintenanceError("原业务字段丢失：" + table)
            fields = ",".join('"' + column.replace('"', '""') + '"' for column in wanted)
            rows = [[_value(v) for v in row] for row in db.execute("SELECT " + fields + " FROM " + quoted)]
            result[table] = dict(columns=wanted, rows=sorted(rows, key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True)))
    return result


def file_hashes(layout):
    files = {}
    # Account uploads and immutable ZIP receipts live beside SQLite. Include
    # their optional trees without changing the running host's layout identity.
    roots = {**layout.roots(), "materials": layout.database.with_suffix(".materials"),
             "media_exports": layout.database.with_suffix(".media-exports")}
    for label, root in roots.items():
        safe_path(root)
        for path in sorted(root.rglob("*")):
            safe_path(path)
            if path.is_file():
                with path.open("rb") as source:
                    fingerprint = hashlib.sha256()
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        fingerprint.update(chunk)
                    files[label + "/" + path.relative_to(root).as_posix()] = fingerprint.hexdigest()
    return files


def snapshot(layout):
    return dict(layout=layout.fingerprint(), tables=_tables(layout), files=file_hashes(layout),
                migrations=_migration_versions(layout))


def _migration_versions(layout):
    from alembic.script import ScriptDirectory
    from creatoros.config import PROJECT_ROOT

    script = ScriptDirectory(str(PROJECT_ROOT / "migrations"))
    known = {revision.revision for revision in script.walk_revisions()}
    with connect(layout) as db:
        versions = sorted(row[0] for row in db.execute("SELECT version_num FROM alembic_version"))
    if not versions or not set(versions) <= known:
        raise MaintenanceError("数据库迁移版本未知，不能自动维护。")
    return versions


def migration_pending(layout):
    """A schema upgrade needs full verification; routine restarts do not."""
    from alembic.script import ScriptDirectory
    from creatoros.config import PROJECT_ROOT

    heads = sorted(ScriptDirectory(str(PROJECT_ROOT / "migrations")).get_heads())
    return _migration_versions(layout) != heads


def _verify_migrations(layout, before):
    from alembic.script import ScriptDirectory
    from creatoros.config import PROJECT_ROOT

    after = _migration_versions(layout)
    if after == before:
        return
    script = ScriptDirectory(str(PROJECT_ROOT / "migrations"))
    heads = sorted(script.get_heads())
    ancestors = {revision.revision for revision in script.walk_revisions()}
    if after != heads or not set(before) <= ancestors:
        raise MaintenanceError("迁移版本不是原版本或当前代码的 head，保持维护状态。")


def verify_snapshot(layout, before):
    if layout.fingerprint() != before["layout"]:
        raise MaintenanceError("受管实例的数据目录已变化。")
    _verify_migrations(layout, before["migrations"])
    if _tables(layout, before["tables"]) != before["tables"]:
        raise MaintenanceError("原数据库记录发生变化，保留备份，请人工核对；未自动回滚。")
    if file_hashes(layout) != before["files"]:
        raise MaintenanceError("旧文件或文件集合发生变化，保留证据；未自动覆盖。")
    with connect(layout) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchall():
            raise MaintenanceError("SQLite完整性或外键检查失败。")
    return dict(original_rows_unchanged=True, files_unchanged=len(before["files"]))


def backup(layout, target):
    target = safe_path(target)
    if not target.is_relative_to(layout.control_dir):
        raise MaintenanceError("备份只能写入本数据库的维护目录。")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb"):
        pass
    destination = sqlite3.connect(target)
    try:
        with connect(layout) as source:
            source.backup(destination)
        if (destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or destination.execute("PRAGMA foreign_key_check").fetchall()):
            raise MaintenanceError("备份核验失败，未停止服务。")
    finally:
        destination.close()
