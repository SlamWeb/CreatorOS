"""P1 数据契约：隔离旧库升级 0005，验证数据不变、组合约束与未分配栏目语义。

只使用临时目录数据库，不触碰正式 data/creatoros.db。
"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from creatoros.config import PROJECT_ROOT
from creatoros.storage import Base, Database, Series, upgrade_database

OLD_REVISION = "20260904_0004"
HEAD_REVISION = "20260923_0005"
TS = "2026-09-20 10:00:00.000000"

SNAPSHOT = {
    "creator_id": "creator-old",
    "series_id": "series-old",
    "series_name": "旧栏目",
    "series_description": "迁移前创建",
    "audience": "老读者",
    "skill_name": "knowledge-to-carousel",
    "topic_id": "topic-old-1",
    "topic_title": "旧选题",
    "topic_brief": "保留原文",
}


def _alembic(direction: str, url: str, revision: str) -> None:
    config = Config(str(Path(PROJECT_ROOT) / "alembic.ini"))
    config.attributes["database_url"] = url
    getattr(command, direction)(config, revision)


def _rows(connection, table: str) -> list[dict]:
    return [dict(row) for row in connection.execute(text(f"SELECT * FROM {table} ORDER BY 1")).mappings()]


def _seed_old_schema(connection) -> None:
    connection.execute(text(
        "INSERT INTO creators (id, display_name, platform, account_handle, timezone, daily_content_limit, is_active, created_at, updated_at)"
        " VALUES ('creator-old', '旧账号', 'xiaohongshu', 'old_lab', 'Asia/Shanghai', NULL, 1, :ts, :ts)"
    ), {"ts": TS})
    connection.execute(text(
        "INSERT INTO series (id, creator_id, name, description, audience, skill_name, selection_policy, publish_policy, replenish_threshold, is_active, created_at, updated_at)"
        " VALUES ('series-old', 'creator-old', '旧栏目', '迁移前创建', '老读者', 'knowledge-to-carousel', 'approval', 'approval', 5, 1, :ts, :ts)"
    ), {"ts": TS})
    connection.execute(text(
        "INSERT INTO topics (id, series_id, title, brief, source, status, position, created_at, updated_at)"
        " VALUES ('topic-old-1', 'series-old', '旧选题', '保留原文', 'manual', 'queued', 1, :ts, :ts),"
        "        ('topic-old-2', 'series-old', '旧选题二', NULL, 'research', 'ready', 2, :ts, :ts)"
    ), {"ts": TS})
    connection.execute(text(
        "INSERT INTO content_runs (id, topic_id, idempotency_key, status, active_revision_number, input_snapshot_json, retryable, version, created_at, updated_at)"
        " VALUES ('run-old', 'topic-old-1', 'content:topic-old-1', 'approved', 1, :snapshot, 0, 3, :ts, :ts)"
    ), {"ts": TS, "snapshot": json.dumps(SNAPSHOT, ensure_ascii=False)})


def _expect_integrity_error(connection, statement: str, params: dict) -> None:
    try:
        connection.execute(text(statement), params)
    except IntegrityError:
        return
    raise AssertionError(f"应被拒绝但未拒绝：{statement}")


def check_data_preservation(url: str) -> None:
    upgrade_database(url, OLD_REVISION)
    engine = create_engine(url)
    with engine.begin() as connection:
        _seed_old_schema(connection)
        before = {table: _rows(connection, table) for table in ("creators", "series", "topics", "content_runs")}
    upgrade_database(url)
    with engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_revision() == HEAD_REVISION
        assert _rows(connection, "creators") == before["creators"]
        assert _rows(connection, "topics") == before["topics"]
        assert _rows(connection, "content_runs") == before["content_runs"], "旧 Run 输入 JSON 等字段必须逐字节不变"
        migrated = _rows(connection, "series")
        assert len(migrated) == len(before["series"]) == 1
        row = migrated[0]
        for key, value in before["series"][0].items():
            assert row[key] == value, f"旧栏目字段 {key} 被迁移修改"
        assert row["mind_skill_id"] is None and row["production_skill_id"] is None
        assert row["revision"] == 1
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
        assert diff == [], f"模型与迁移结果存在漂移：{diff}"
    # 降级回旧结构：纯旧数据必须完整保留。
    _alembic("downgrade", url, OLD_REVISION)
    with engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_revision() == OLD_REVISION
        assert _rows(connection, "series") == before["series"]
        assert _rows(connection, "content_runs") == before["content_runs"]
    # 再次升级后仍一致（可重复迁移）。
    upgrade_database(url)
    with engine.connect() as connection:
        assert _rows(connection, "content_runs") == before["content_runs"]
    engine.dispose()


def check_contract_constraints(url: str) -> None:
    upgrade_database(url)
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO creators (id, display_name, platform, timezone, is_active, created_at, updated_at)"
            " VALUES ('c1', '账号一', 'xiaohongshu', 'Asia/Shanghai', 1, :ts, :ts)"
        ), {"ts": TS})
        # 未分配的旧单 Skill 栏目可以存在。
        connection.execute(text(
            "INSERT INTO series (id, creator_id, name, description, audience, skill_name, selection_policy, publish_policy, replenish_threshold, revision, is_active, created_at, updated_at)"
            " VALUES ('s-unassigned', NULL, '未分配栏目', 'd', 'a', 'knowledge-to-carousel', 'approval', 'approval', 5, 1, 1, :ts, :ts)"
        ), {"ts": TS})
        # 未分配栏目同名被部分唯一索引拒绝。
        _expect_integrity_error(connection, (
            "INSERT INTO series (id, creator_id, name, description, audience, skill_name, selection_policy, publish_policy, replenish_threshold, revision, is_active, created_at, updated_at)"
            " VALUES ('s-dup', NULL, '未分配栏目', 'd', 'a', 'knowledge-to-carousel', 'approval', 'approval', 5, 1, 1, :ts, :ts)"
        ), {"ts": TS})
        # 完整 mind+production 组合可以存在（skill_name 必须为空）。
        connection.execute(text(
            "INSERT INTO series (id, creator_id, name, description, audience, skill_name, mind_skill_id, production_skill_id, selection_policy, publish_policy, replenish_threshold, revision, is_active, created_at, updated_at)"
            " VALUES ('s-pair', NULL, '组合栏目', 'd', 'a', NULL, 'mind-skill--aaaaaaaaaaaaaaaa', 'carousel-skill--bbbbbbbbbbbbbbbb', 'approval', 'approval', 5, 1, 1, :ts, :ts)"
        ), {"ts": TS})
        # 半套组合（只绑 mind）被拒绝。
        _expect_integrity_error(connection, (
            "INSERT INTO series (id, creator_id, name, description, audience, skill_name, mind_skill_id, selection_policy, publish_policy, replenish_threshold, revision, is_active, created_at, updated_at)"
            " VALUES ('s-half', NULL, '半套', 'd', 'a', NULL, 'mind-skill--aaaaaaaaaaaaaaaa', 'approval', 'approval', 5, 1, 1, :ts, :ts)"
        ), {"ts": TS})
        # 组合与旧 skill_name 同时出现被拒绝。
        _expect_integrity_error(connection, (
            "INSERT INTO series (id, creator_id, name, description, audience, skill_name, mind_skill_id, production_skill_id, selection_policy, publish_policy, replenish_threshold, revision, is_active, created_at, updated_at)"
            " VALUES ('s-both', NULL, '混合', 'd', 'a', 'knowledge-to-carousel', 'm--aaaaaaaaaaaaaaaa', 'p--bbbbbbbbbbbbbbbb', 'approval', 'approval', 5, 1, 1, :ts, :ts)"
        ), {"ts": TS})
        # revision 必须为正。
        _expect_integrity_error(connection, (
            "INSERT INTO series (id, creator_id, name, description, audience, skill_name, selection_policy, publish_policy, replenish_threshold, revision, is_active, created_at, updated_at)"
            " VALUES ('s-rev0', NULL, '零版本', 'd', 'a', 'knowledge-to-carousel', 'approval', 'approval', 5, 0, 1, :ts, :ts)"
        ), {"ts": TS})
        # 已分配栏目的 (creator_id, name) 唯一约束保持有效。
        connection.execute(text(
            "INSERT INTO series (id, creator_id, name, description, audience, skill_name, selection_policy, publish_policy, replenish_threshold, revision, is_active, created_at, updated_at)"
            " VALUES ('s-own', 'c1', '同名', 'd', 'a', 'knowledge-to-carousel', 'approval', 'approval', 5, 1, 1, :ts, :ts)"
        ), {"ts": TS})
        _expect_integrity_error(connection, (
            "INSERT INTO series (id, creator_id, name, description, audience, skill_name, selection_policy, publish_policy, replenish_threshold, revision, is_active, created_at, updated_at)"
            " VALUES ('s-own-dup', 'c1', '同名', 'd', 'a', 'knowledge-to-carousel', 'approval', 'approval', 5, 1, 1, :ts, :ts)"
        ), {"ts": TS})
        indexes = {row["name"] for row in connection.execute(text("PRAGMA index_list('series')")).mappings()}
        assert "uq_series_unassigned_name" in indexes
    # ORM 读取旧栏目与未分配栏目不报错，组合字段为 None。
    database = Database(url)
    with database.session() as session:
        legacy = session.get(Series, "s-own")
        assert legacy.revision == 1 and legacy.mind_skill_id is None and legacy.skill_name == "knowledge-to-carousel"
        unassigned = session.get(Series, "s-unassigned")
        assert unassigned.creator_id is None and unassigned.creator is None
    database.close()
    engine.dispose()


def main() -> None:
    with TemporaryDirectory() as tmp:
        check_data_preservation(f"sqlite:///{(Path(tmp) / 'upgrade.db').as_posix()}")
    with TemporaryDirectory() as tmp:
        check_contract_constraints(f"sqlite:///{(Path(tmp) / 'contract.db').as_posix()}")
    print("series_composition_migration_smoke=passed")


if __name__ == "__main__":
    main()
