"""P2-S6 真实验收：从 GitHub 安装 mind/production 两个真实 Skill 并组成 pair 栏目。

真实网络仅用于公开仓库 git clone；不调用模型、不生图、不碰正式数据库。
运行：D:/Anaconda4.7g/envs/deepcode/python.exe -m tests.live_composition_skills
"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from creatoros.integrations.producer_skills import ProducerSkillCatalog, SkillInstallService, skills_root_for
from creatoros.runs import ContentRunError, ContentRunService
from creatoros.storage import Creator, Database, Series, Topic, TopicSource, upgrade_database
from creatoros.web.composition import SeriesCompositionService

MIND_URL = "https://github.com/SlamWeb/knowledge-to-storyboard/tree/main/skills/knowledge-to-storyboard-deep"
PRODUCTION_URL = "https://github.com/SlamWeb/creatorOS-ip-skills/tree/main/xiaobai"


def _install(service: SkillInstallService, url: str, role: str) -> dict:
    job = service.submit(url, role=role)
    service.thread.join(120)
    finished = service.get(job["id"])
    assert finished["status"] == "installed", f"{url} 安装失败：{finished['message']}"
    return finished["skill"]


def main() -> None:
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        url = f"sqlite:///{(root / 'creatoros.db').as_posix()}"
        upgrade_database(url)
        database = Database(url)
        service = SkillInstallService(ProducerSkillCatalog(skills_root_for(database)))
        service.start()

        mind = _install(service, MIND_URL, "mind")
        production = _install(service, PRODUCTION_URL, "production")
        assert mind["role"] == "mind" and mind["name"] == "knowledge-to-storyboard-deep"
        assert production["role"] == "production" and production["name"] == "xiaobai"
        # 两个真实 Skill 都不声明轮播产物契约：可安装、可组合，当前不可生产。
        assert mind["carousel_compatible"] is False and production["carousel_compatible"] is False

        listed = {item["id"]: item for item in service.catalog.list()}
        assert listed[mind["id"]]["producible"] is False
        assert listed[production["id"]]["producible"] is False
        # xiaobai 的角色参考图等资产随版本一起进入受管目录。
        assert (service.catalog.locate(production["id"]) / "assets" / "character.png").is_file()

        composition = SeriesCompositionService(database, service.catalog)
        series_id, deduplicated = composition.create_series(
            name="AI 概念图解", description="把 AI/Agent 工程概念讲懂", audience="零基础开发者",
            creator_id=None, skill_name=None,
            mind_skill_id=mind["id"], production_skill_id=production["id"],
            request_id=uuid4().hex, origin="agent",
        )
        assert not deduplicated

        # 组合可保存为未验证；分配账号后生产仍被明确拦截（P4 才接入双 Skill 生产）。
        with database.session() as session:
            series = session.get(Series, series_id)
            assert series.mind_skill_id == mind["id"] and series.creator_id is None
            session.add(Topic(id="topic-live-1", series_id=series_id, title="什么是 Checkpoint",
                              source=TopicSource.MANUAL, position=1))
        runs = ContentRunService(database, output_root=root / "outputs")
        try:
            runs.create("topic-live-1")
            raise AssertionError("未分配栏目不应可生产")
        except ContentRunError as error:
            assert "尚未分配账号" in str(error)
        with database.session() as session:
            session.add(Creator(id="creator-live", display_name="验收账号", platform="xiaohongshu"))
        composition.assign_series(series_id, creator_id="creator-live", expected_revision=1,
                                  request_id=uuid4().hex, origin="agent")
        try:
            runs.create("topic-live-1")
            raise AssertionError("pair 栏目不应进入旧生产路径")
        except ContentRunError as error:
            assert "双 Skill" in str(error)

        service.shutdown()
        database.close()
    print(json.dumps({"mind": mind["id"], "production": production["id"], "series_id": series_id},
                     ensure_ascii=False))
    print("live_composition_skills=passed")


if __name__ == "__main__":
    main()
