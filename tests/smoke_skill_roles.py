"""P1：Skill 目录区分可安装、角色与产物能力；旧记录只读映射，不回写。

全部使用本地临时 git 仓库夹具，不联网、不安装真实 Skill、不改正式目录。
"""
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from creatoros.integrations.producer_skills import (
    InstallReceipt,
    ProducerSkillCatalog,
    SkillInstallService,
)


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


def _expect_value_error(fn, fragment: str) -> None:
    try:
        fn()
    except ValueError as error:
        assert fragment in str(error), f"错误信息应包含 {fragment!r}，实际：{error}"
        return
    raise AssertionError(f"应拒绝但通过：{fragment}")


def main() -> None:
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        catalog = ProducerSkillCatalog(root / "skills")

        # 内置 Skill：旧单 Skill 角色，可生产，可只读定位。
        builtin = catalog.list()[0]
        assert builtin["id"] == ProducerSkillCatalog.BUILTIN_ID
        assert builtin["role"] == "legacy_end_to_end" and builtin["producible"] is True
        assert catalog.resolve(ProducerSkillCatalog.BUILTIN_ID).is_dir()
        assert catalog.locate(ProducerSkillCatalog.BUILTIN_ID).is_dir()

        # mind 角色：可展示、可 locate 供调研上下文，生产门禁 resolve 拒绝。
        mind = catalog.register(root / "w-mind", "https://github.com/example/mind-notes",
                                _fixture(root / "w-mind", "mind-notes", compatible=False), role="mind")
        listed = {item["id"]: item for item in catalog.list()}
        assert listed[mind["id"]]["role"] == "mind" and listed[mind["id"]]["producible"] is False
        assert catalog.locate(mind["id"]).is_dir()
        _expect_value_error(lambda: catalog.resolve(mind["id"]), "内容 Skill")

        # production 角色且满足轮播契约：可生产。
        producer = catalog.register(root / "w-prod", "https://github.com/example/prod-carousel",
                                    _fixture(root / "w-prod", "prod-carousel", compatible=True), role="production")
        assert catalog.resolve(producer["id"]).is_dir()
        assert {item["id"]: item for item in catalog.list()}[producer["id"]]["producible"] is True

        # production 角色但产物契约不满足（如纯文本 Skill）：展示为不可生产，resolve 拒绝。
        text_skill = catalog.register(root / "w-text", "https://github.com/example/text-maker",
                                      _fixture(root / "w-text", "text-maker", compatible=False), role="production")
        listed = {item["id"]: item for item in catalog.list()}
        assert listed[text_skill["id"]]["producible"] is False
        _expect_value_error(lambda: catalog.resolve(text_skill["id"]), "图片轮播")

        # 未分类（安装时未声明角色）：允许展示，不能绑定生产。
        plain = catalog.register(root / "w-plain", "https://github.com/example/plain-skill",
                                 _fixture(root / "w-plain", "plain-skill", compatible=True))
        listed = {item["id"]: item for item in catalog.list()}
        assert listed[plain["id"]]["role"] is None and listed[plain["id"]]["producible"] is False
        _expect_value_error(lambda: catalog.resolve(plain["id"]), "尚未声明")
        assert catalog.locate(plain["id"]).is_dir()

        # 旧注册记录（无 role 键）：读取时映射 legacy_end_to_end，磁盘文件不被改写。
        record_path = root / "skills" / "registry" / f"{producer['id']}.json"
        legacy_raw = {key: value for key, value in json.loads(record_path.read_text(encoding="utf-8")).items() if key != "role"}
        record_path.write_text(json.dumps(legacy_raw, ensure_ascii=False), encoding="utf-8")
        before_bytes = record_path.read_bytes()
        listed = {item["id"]: item for item in catalog.list()}
        assert listed[producer["id"]]["role"] == "legacy_end_to_end" and listed[producer["id"]]["producible"] is True
        assert catalog.resolve(producer["id"]).is_dir()
        assert record_path.read_bytes() == before_bytes, "读取目录时不得回写历史注册文件"

        # 角色白名单：注册与安装提交都拒绝未知角色。
        _expect_value_error(
            lambda: catalog.register(root / "w-prod", "https://github.com/example/prod-carousel",
                                     InstallReceipt(skill_path=".", carousel_compatible=True, compatibility_note="x"),
                                     role="wizard"),
            "角色",
        )
        service = SkillInstallService(ProducerSkillCatalog(root / "skills-2"))
        _expect_value_error(lambda: service.submit("https://github.com/example/x", role="wizard"), "角色")

        # 未知 Skill ID：两个入口都拒绝。
        _expect_value_error(lambda: catalog.resolve("nosuch--0000000000000000"), "未安装")
        _expect_value_error(lambda: catalog.locate("nosuch--0000000000000000"), "未安装")
        service.shutdown()
    print("skill_roles_smoke=passed")


if __name__ == "__main__":
    main()
