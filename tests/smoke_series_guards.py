"""P1 服务守卫：未分配/双 Skill 栏目显式拒绝生产，调研显式分支，旧 Run 快照不变。

隔离临时数据库与 Skill 目录；不调用模型、Codex、生图或真实安装。
"""
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from sqlalchemy import text

from creatoros.integrations.producer_skills import InstallReceipt, ProducerSkillCatalog
from creatoros.integrations.topic_research import TopicResearchService
from creatoros.operations.executor import OperationExecutor
from creatoros.operations.models import OperationPlan
from creatoros.operations.parser import validate_scope
from creatoros.runs import ContentRunError, ContentRunService
from creatoros.storage import (
    ContentRepository,
    Creator,
    CreatorPlatform,
    Database,
    Series,
    Topic,
    TopicSource,
    upgrade_database,
)
from creatoros.web import create_app


def _mind_fixture(workspace: Path) -> InstallReceipt:
    source = workspace / "source"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\nname: interview-angles\ndescription: 面试题内容切入点\n---\n内容方法，不产出轮播。\n",
        encoding="utf-8",
    )
    for args in [("init",), ("add", "."), ("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture"),
                 ("remote", "add", "origin", "https://github.com/example/interview-angles")]:
        subprocess.run(["git", "-C", str(source), *args], check=True, capture_output=True)
    return InstallReceipt(skill_path=".", carousel_compatible=False, compatibility_note="fixture")


def _series(series_id: str, *, creator_id, skill_name="knowledge-to-carousel", mind=None, production=None) -> Series:
    return Series(
        id=series_id, creator_id=creator_id, name=f"栏目-{series_id}", description="d", audience="a",
        skill_name=skill_name, mind_skill_id=mind, production_skill_id=production,
    )


def main() -> None:
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        url = f"sqlite:///{(root / 'guards.db').as_posix()}"
        upgrade_database(url)
        database = Database(url)
        with database.session() as session:
            session.add(Creator(id="creator-1", display_name="账号一", platform=CreatorPlatform.XIAOHONGSHU))
            session.add(_series("series-legacy", creator_id="creator-1"))
            session.add(_series("series-unassigned", creator_id=None))
            session.add(_series("series-pair", creator_id="creator-1", skill_name=None,
                                mind="interview-angles--placeholder", production="prod--placeholder"))
            for index, series_id in enumerate(("series-legacy", "series-unassigned", "series-pair"), start=1):
                session.add(Topic(id=f"topic-{index}", series_id=series_id, title=f"选题{index}",
                                  source=TopicSource.MANUAL, position=1))

        runs = ContentRunService(database, output_root=root / "outputs")
        # 旧栏目：正常创建，输入快照键与 0005 之前完全一致（无组合字段）。
        run = runs.create("topic-1")
        assert set(run.input_snapshot_json) == {
            "creator_id", "series_id", "series_name", "series_description", "audience",
            "skill_name", "topic_id", "topic_title", "topic_brief",
        }, f"旧 Run 输入快照键漂移：{sorted(run.input_snapshot_json)}"
        assert run.input_snapshot_json["skill_name"] == "knowledge-to-carousel"

        # 未分配栏目：明确拒绝生产，不是 AttributeError 或校验崩溃。
        try:
            runs.create("topic-2")
            raise AssertionError("未分配栏目不应可生产")
        except ContentRunError as error:
            assert "尚未分配账号" in str(error)
        # 双 Skill 栏目：明确提示未接入。
        try:
            runs.create("topic-3")
            raise AssertionError("双 Skill 栏目不应进入旧生产路径")
        except ContentRunError as error:
            assert "双 Skill" in str(error)

        # 调研：未分配旧栏目可用（候选只是建议）；双 Skill 栏目走 mind 定位。
        catalog = ProducerSkillCatalog(root / "skills")
        research = TopicResearchService(database, catalog, researcher=object())
        legacy_snapshot = research.snapshot("series-unassigned")
        assert legacy_snapshot["skill_digest"] and legacy_snapshot["skill_text"]
        mind_record = catalog.register(root / "w-mind", "https://github.com/example/interview-angles",
                                       _mind_fixture(root / "w-mind"), role="mind")
        with database.session() as session:
            pair = session.get(Series, "series-pair")
            pair.mind_skill_id = mind_record["id"]
            pair.production_skill_id = "knowledge-to-carousel"
        pair_snapshot = research.snapshot("series-pair")
        assert "面试题内容切入点" in pair_snapshot["skill_text"]

        # 计划执行：未分配栏目允许管理选题（P2 起），生产才要求账号。
        repository = ContentRepository(database)
        validate_scope(repository, "series-unassigned")  # 不抛错：允许作为选题计划范围
        plan = OperationPlan(operations=[
            {"action": "add_topics", "series_id": "series-unassigned",
             "topics": [{"topic_id": "topic-x", "title": "x"}]},
        ])
        preview = OperationExecutor(repository).preview(plan)
        assert preview.confirmation_token

        # A 策略直接入队：原子完成、写审计事件、同 request_id 重放不重复写入。
        from creatoros.operations import PendingOperationService
        pending_service = PendingOperationService(database, parser=None)
        direct_plan = OperationPlan(operations=[
            {"action": "add_topics", "series_id": "series-unassigned",
             "topics": [{"topic_id": "topic-direct-1", "title": "直入队一", "brief": "b", "source": "research"},
                        {"topic_id": "topic-direct-2", "title": "直入队二", "source": "research"}]},
        ])
        pending, deduplicated = pending_service.execute_direct(
            "直接入队 2 条选题", direct_plan,
            scope_series_id="series-unassigned", request_id="req-direct-1", origin="agent",
        )
        assert deduplicated is False and pending.status.value == "succeeded"
        replay, deduplicated = pending_service.execute_direct(
            "直接入队 2 条选题", direct_plan,
            scope_series_id="series-unassigned", request_id="req-direct-1", origin="agent",
        )
        assert deduplicated is True and replay.id == pending.id
        with database.session() as session:
            titles = [t.title for t in session.query(Topic).filter_by(series_id="series-unassigned").order_by(Topic.position)]
            assert titles == ["选题2", "直入队一", "直入队二"], titles
            events = session.execute(
                text("SELECT event_type FROM operation_events WHERE pending_operation_id = :id ORDER BY id"),
                {"id": pending.id},
            ).fetchall()
            assert [row[0] for row in events] == ["proposed", "confirmed", "succeeded"]

        # API 读路径：未分配与双 Skill 栏目可见，字段为 null 而非崩溃。
        app = create_app(database=database, run_service=runs)
        with TestClient(app) as client:
            unassigned = client.get("/api/series/series-unassigned").json()
            assert unassigned["creator_id"] is None and unassigned["skill_name"] == "knowledge-to-carousel"
            assert unassigned["mind_skill_id"] is None and unassigned["revision"] >= 1
            pair = client.get("/api/series/series-pair").json()
            assert pair["skill_name"] is None and pair["mind_skill_id"] == mind_record["id"]
            legacy = client.get("/api/series/series-legacy").json()
            assert legacy["creator_id"] == "creator-1" and legacy["skill_name"] == "knowledge-to-carousel"
        database.close()
    print("series_guards_smoke=passed")


if __name__ == "__main__":
    main()
