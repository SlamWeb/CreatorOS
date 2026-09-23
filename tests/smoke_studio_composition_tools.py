"""P2-S4：Agent 工具与 HTTP 跨入口一致性——同一对象经 Tool 写入、HTTP 读取一致。

真实本地 HTTP 服务 + 隔离 SQLite/Skill 目录；不调用模型、不联网、不生产。
"""
import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from creatoros.ai.types import ToolCall
from creatoros.integrations.producer_skills import (
    InstallReceipt,
    ProducerSkillCatalog,
    SkillInstallService,
    skills_root_for,
)
from creatoros.integrations.studio import StudioClient
from creatoros.storage import Database, upgrade_database
from creatoros.tools import execute_tool_call, tools
from creatoros.web import create_app
from tests.agent_studio_support import serve


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


def _tool(tool_name, **args):
    return execute_tool_call(ToolCall("test", tool_name, json.dumps(args)), model_requested=True)


def main() -> None:
    exposed = {item["function"]["name"] for item in tools}
    for name in ("compose_series", "update_series_composition", "assign_series", "queue_topics"):
        assert name in exposed, f"工具未注册：{name}"

    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        url = f"sqlite:///{(root / 'tools.db').as_posix()}"
        upgrade_database(url)
        database = Database(url)
        catalog = ProducerSkillCatalog(skills_root_for(database))
        mind = catalog.register(root / "w-mind", "https://github.com/example/mind-skill",
                                _fixture(root / "w-mind", "mind-skill", compatible=False), role="mind")
        production = catalog.register(root / "w-prod", "https://github.com/example/prod-skill",
                                      _fixture(root / "w-prod", "prod-skill", compatible=True), role="production")
        installs = SkillInstallService(catalog)
        app = create_app(database=database, skill_install_service=installs)
        with serve(app) as base, patch.dict(os.environ, {"CREATOROS_STUDIO_URL": base}):
            client = StudioClient(base)
            creator = client.request("POST", "/api/creators", payload={"display_name": "跨入口账号"})

            # Agent 工具创建 pair 栏目（未分配）。
            created = _tool("compose_series", name="跨端栏目", description="d", audience="a",
                            mind_skill_id=mind["id"], production_skill_id=production["id"])
            assert not created.is_error, created.content
            series_id = json.loads(created.content)["series"]["id"]
            # HTTP 读取同一对象：字段一致。
            via_http = client.request("GET", f"/api/series/{series_id}")
            assert via_http["mind_skill_id"] == mind["id"] and via_http["creator_id"] is None
            assert via_http["revision"] == 1

            # 半套组合被工具侧服务拒绝（不是静默成功）。
            half = _tool("compose_series", name="半套", mind_skill_id=mind["id"])
            assert half.is_error

            # 分配账号：先拿旧 revision 会被拒，再取最新 revision 成功。
            stale = _tool("assign_series", series_id=series_id, creator_id=creator["id"], expected_revision=99)
            assert stale.is_error and "revision" in json.loads(stale.content)["error"]
            assigned = _tool("assign_series", series_id=series_id, creator_id=creator["id"], expected_revision=1)
            assert not assigned.is_error, assigned.content
            assert json.loads(assigned.content)["series"]["revision"] == 2
            assert client.request("GET", f"/api/series/{series_id}")["creator_id"] == creator["id"]

            # 直入队：Agent 工具写入，HTTP 读取到相同选题。
            queued = _tool("queue_topics", series_id=series_id,
                           topics=[{"title": "跨端选题一", "brief": "b", "source": "research"}],
                           summary="用户：把选题一入队")
            assert not queued.is_error, queued.content
            topics = client.request("GET", f"/api/series/{series_id}/topics")["items"]
            assert [t["title"] for t in topics] == ["跨端选题一"]

            # 改组合：换成另一个制作 Skill，revision 继续推进。
            other = catalog.register(root / "w-prod2", "https://github.com/example/prod-skill-2",
                                     _fixture(root / "w-prod2", "prod-skill-2", compatible=True), role="production")
            moved = _tool("update_series_composition", series_id=series_id,
                          mind_skill_id=mind["id"], production_skill_id=other["id"], expected_revision=2)
            assert not moved.is_error, moved.content
            assert json.loads(moved.content)["series"]["revision"] == 3
            assert json.loads(moved.content)["series"]["production_skill_id"] == other["id"]
            client.close()
        database.close()
        installs.shutdown()
    print("studio_composition_tools_smoke=passed")


if __name__ == "__main__":
    main()
