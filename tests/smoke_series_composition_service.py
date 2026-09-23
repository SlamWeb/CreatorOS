"""P2-S2：组合栏目写服务——创建/改组合/分配的 HTTP 契约、幂等与 revision CAS。

隔离临时库与本地 git 夹具 Skill；不联网、不调用模型或生产。
"""
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from fastapi.testclient import TestClient

from creatoros.integrations.producer_skills import (
    InstallReceipt,
    ProducerSkillCatalog,
    SkillInstallService,
    skills_root_for,
)
from creatoros.storage import Database, Series, upgrade_database
from creatoros.web import create_app


def _fixture(workspace: Path, name: str, compatible: bool) -> InstallReceipt:
    source = workspace / "source"
    source.mkdir(parents=True)
    marker = "creatoros-output: social-content-pack.image-carousel\n" if compatible else ""
    (source / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} fixture\n---\n{marker}body\n",
        encoding="utf-8",
    )
    for args in [("init",), ("add", "."), ("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture"),
                 ("remote", "add", "origin", f"https://github.com/example/{name}")]:
        subprocess.run(["git", "-C", str(source), *args], check=True, capture_output=True)
    return InstallReceipt(skill_path=".", carousel_compatible=compatible, compatibility_note="fixture")


def _rid() -> str:
    return uuid4().hex


def main() -> None:
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        url = f"sqlite:///{(root / 'compose.db').as_posix()}"
        upgrade_database(url)
        database = Database(url)
        catalog = ProducerSkillCatalog(skills_root_for(database))
        mind = catalog.register(root / "w-mind", "https://github.com/example/mind-skill",
                                _fixture(root / "w-mind", "mind-skill", compatible=False), role="mind")
        production = catalog.register(root / "w-prod", "https://github.com/example/prod-skill",
                                      _fixture(root / "w-prod", "prod-skill", compatible=True), role="production")
        installs = SkillInstallService(catalog)
        app = create_app(database=database, skill_install_service=installs)
        with TestClient(app) as client:
            creator = client.post("/api/creators", json={"display_name": "组合账号"}).json()

            # 创建 pair 栏目（先不分配账号）。
            rid = _rid()
            created = client.post("/api/series", json={
                "name": "Agent 图解", "description": "d", "audience": "a",
                "mind_skill_id": mind["id"], "production_skill_id": production["id"],
                "request_id": rid,
            })
            assert created.status_code == 201, created.text
            body = created.json()
            series_id = body["series"]["id"]
            assert body["deduplicated"] is False and body["request_id"] == rid
            assert body["series"]["creator_id"] is None and body["series"]["revision"] == 1
            assert body["series"]["skill_name"] is None
            assert body["series"]["mind_skill_id"] == mind["id"]

            # 幂等：同 request_id 重放返回同一栏目，不产生第二条。
            replay = client.post("/api/series", json={
                "name": "Agent 图解", "description": "d", "audience": "a",
                "mind_skill_id": mind["id"], "production_skill_id": production["id"],
                "request_id": rid,
            })
            assert replay.status_code == 201 and replay.json()["deduplicated"] is True
            assert replay.json()["series"]["id"] == series_id
            with database.session() as session:
                assert session.query(Series).filter_by(name="Agent 图解").count() == 1

            # 同一 request_id 换个操作 → 409。
            reused = client.post(f"/api/series/{series_id}/assignment", json={
                "creator_id": creator["id"], "expected_revision": 1, "request_id": rid,
            })
            assert reused.status_code == 409, reused.text

            # 半套 / 全空 / legacy+pair 混合都被 422 拒绝。
            for bad in (
                {"name": "半套", "mind_skill_id": mind["id"], "request_id": _rid()},
                {"name": "全空", "request_id": _rid()},
                {"name": "混合", "skill_name": "knowledge-to-carousel",
                 "mind_skill_id": mind["id"], "production_skill_id": production["id"], "request_id": _rid()},
            ):
                response = client.post("/api/series", json=bad)
                assert response.status_code == 422, (bad["name"], response.text)

            # 角色放错槽位 / 未安装 Skill / 不存在的账号。
            wrong_slot = client.post("/api/series", json={
                "name": "错槽", "mind_skill_id": production["id"], "production_skill_id": production["id"],
                "request_id": _rid(),
            })
            assert wrong_slot.status_code == 422 and "槽位" in wrong_slot.text
            missing_skill = client.post("/api/series", json={
                "name": "未装", "mind_skill_id": "nosuch--0000000000000000",
                "production_skill_id": production["id"], "request_id": _rid(),
            })
            assert missing_skill.status_code == 422
            missing_creator = client.post("/api/series", json={
                "name": "无账号", "creator_id": "creator-nope", "skill_name": "knowledge-to-carousel",
                "request_id": _rid(),
            })
            assert missing_creator.status_code == 404

            # 分配账号：revision CAS；旧 revision 拒绝且零写。
            conflict = client.post(f"/api/series/{series_id}/assignment", json={
                "creator_id": creator["id"], "expected_revision": 99, "request_id": _rid(),
            })
            assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "revision_conflict"
            assigned = client.post(f"/api/series/{series_id}/assignment", json={
                "creator_id": creator["id"], "expected_revision": 1, "request_id": _rid(),
            })
            assert assigned.status_code == 200, assigned.text
            assert assigned.json()["series"]["creator_id"] == creator["id"]
            assert assigned.json()["series"]["revision"] == 2

            # 改组合：legacy 栏目拒绝转换；pair 栏目 CAS 成功。
            legacy = client.post(f"/api/creators/{creator['id']}/series", json={"name": "旧栏目"}).json()
            rejected = client.post(f"/api/series/{legacy['id']}/composition", json={
                "mind_skill_id": mind["id"], "production_skill_id": production["id"],
                "expected_revision": 1, "request_id": _rid(),
            })
            assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "legacy_series"

            other_prod = catalog.register(root / "w-prod2", "https://github.com/example/prod-skill-2",
                                          _fixture(root / "w-prod2", "prod-skill-2", compatible=True), role="production")
            updated = client.post(f"/api/series/{series_id}/composition", json={
                "mind_skill_id": mind["id"], "production_skill_id": other_prod["id"],
                "expected_revision": 2, "request_id": _rid(),
            })
            assert updated.status_code == 200, updated.text
            assert updated.json()["series"]["production_skill_id"] == other_prod["id"]
            assert updated.json()["series"]["revision"] == 3

            # 撤回分配（creator_id=null）。
            unassigned = client.post(f"/api/series/{series_id}/assignment", json={
                "creator_id": None, "expected_revision": 3, "request_id": _rid(),
            })
            assert unassigned.status_code == 200 and unassigned.json()["series"]["creator_id"] is None

            # Agent 入口来源被记录。
            agent_write = client.post("/api/series", headers={"x-creatoros-origin": "agent"}, json={
                "name": "Agent 创建的栏目", "skill_name": "knowledge-to-carousel", "request_id": _rid(),
            })
            assert agent_write.status_code == 201
            with database.session() as session:
                from creatoros.storage import WriteReceipt
                receipt = session.get(WriteReceipt, agent_write.json()["request_id"])
                assert receipt.origin == "agent"

            # A 策略直接入队：一次 HTTP 完成校验/写入/审计；重放不重复；旧 Preview 路径不受影响。
            queue_body = {"topics": [{"title": "直接入队 A", "source": "research"}, {"title": "直接入队 B"}],
                          "request_id": _rid()}
            queued = client.post(f"/api/series/{series_id}/queue", json=queue_body)
            assert queued.status_code == 201, queued.text
            assert queued.json()["deduplicated"] is False and len(queued.json()["topic_ids"]) == 2
            replayed = client.post(f"/api/series/{series_id}/queue", json=queue_body)
            assert replayed.status_code == 201 and replayed.json()["deduplicated"] is True
            topics = client.get(f"/api/series/{series_id}/topics").json()["items"]
            assert [t["title"] for t in topics] == ["直接入队 A", "直接入队 B"]
        database.close()
        installs.shutdown()
    print("series_composition_service_smoke=passed")


if __name__ == "__main__":
    main()
