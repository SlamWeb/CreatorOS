from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from creatoros.agent.loop import build_model_context
from creatoros.session.snapshot import new_messages
from creatoros.skills.loader import SkillLoader, parse_skill_frontmatter
from creatoros.tools.definitions import tools


def write_skill(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def check_frontmatter_parser():
    for metadata in (
        {"name": "quoted", "description": '讲故事: "中文" # 不是注释'},
        {"name": "escaped", "description": "第一行\n第二行：解释", "creatoros-display-name": "柯基漫画"},
    ):
        # Cover PyYAML's escaped Unicode output as well as human-written Chinese.
        for allow_unicode in (False, True):
            content = "---\n" + yaml.safe_dump(metadata, allow_unicode=allow_unicode) + "---\n正文"
            assert parse_skill_frontmatter(content) == metadata

    samples = (
        ('name: "quoted"\ndescription: "A: B # C\\nD"', "A: B # C\nD"),
        ("name: folded\ndescription: >-\n  第一段\n  第二段", "第一段 第二段"),
        ("name: literal\ndescription: |\n  第一段\n  第二段", "第一段\n第二段\n"),
        ("name: inline-comment\ndescription: 内容 # 备注", "内容"),
    )
    for frontmatter, expected in samples:
        actual = parse_skill_frontmatter(f"---\n{frontmatter}\n---\n正文")["description"]
        assert actual == expected

    invalid = (
        ("# no metadata", "起始标记"),
        ("---\nname: unfinished", "结束标记"),
        ("---\n- item\n---", "键值对象"),
        ("---\n---", "键值对象"),
        ("---\nname: [broken\n---", "格式无效"),
        ("---\nname: !!python/object/apply:os.system ['echo unsafe']\n---", "格式无效"),
    )
    invalid_metadata = (
        ({"name": True, "description": "x"}, "name"),
        ({"name": "Upper", "description": "x"}, "name"),
        ({"name": "a" * 65, "description": "x"}, "name"),
        ({"name": "valid", "description": ["not", "text"]}, "description"),
        ({"name": "valid", "description": "   "}, "description"),
        ({"name": "valid", "description": "x" * 1025}, "description"),
    )
    invalid += tuple(
        ("---\n" + yaml.safe_dump(metadata) + "---", field)
        for metadata, field in invalid_metadata
    )
    invalid += tuple(
        (
            "---\n" + yaml.safe_dump({"name": "valid", "description": "x", "creatoros-display-name": display}) + "---",
            "creatoros-display-name",
        )
        for display in (None, True, "", "x" * 81, "两\n行", "制\t表", "隐\u200b藏", "两\u2028行")
    )
    for content, expected in invalid:
        try:
            parse_skill_frontmatter(content)
        except ValueError as error:
            assert expected in str(error), (expected, str(error))
        else:
            raise AssertionError(f"未拒绝无效 frontmatter: {expected}")


def main():
    check_frontmatter_parser()
    with TemporaryDirectory() as temporary:
        root = Path(temporary) / "skills"
        write_skill(
            root / "alpha" / "SKILL.md",
            "---\nname: alpha\ndescription: 做一件 Alpha 任务\n---\n\n# Alpha\n按步骤执行。\n",
        )
        write_skill(root / "broken" / "SKILL.md", "# missing frontmatter\n")
        write_skill(
            root / "duplicate" / "SKILL.md",
            "---\nname: alpha\ndescription: duplicate\n---\n",
        )

        loader = SkillLoader([root], project_root=Path(temporary))
        skills = loader.discover()
        assert [skill.name for skill in skills] == ["alpha"]
        assert any("缺少 YAML" in item.message for item in loader.diagnostics)
        assert any("重复" in item.message for item in loader.diagnostics)

        loaded = loader.load("alpha")
        assert loaded.content and "按步骤执行" in loaded.content
        available = loader.format_available_prompt()
        assert "<available_skills>" in available
        assert "skills/alpha/SKILL.md" in available
        invocation = loader.format_invocation("alpha", "用户补充要求")
        assert "<skill name=\"alpha\"" in invocation
        assert invocation.endswith("用户补充要求")

        write_skill(
            root / "folded" / "SKILL.md",
            "---\nname: folded\ncreatoros-display-name: 柯基漫画\ndescription: >-\n  用故事解释\n  新主题。\n---\n正文",
        )
        write_skill(root / "invalid-yaml" / "SKILL.md", "---\nname: [broken\n---")
        refreshed = loader.reload()
        assert [skill.name for skill in refreshed] == ["alpha", "folded"]
        assert refreshed[1].description == "用故事解释 新主题。"
        assert refreshed[1].display_name == "柯基漫画"
        assert any("格式无效" in item.message for item in loader.diagnostics)
        assert loader.load("folded").content.endswith("正文")
        assert loader.load("folded").display_name == "柯基漫画"

        messages = [{"role": "system", "content": "基础提示"}, {"role": "user", "content": "你好"}]
        enriched = loader.inject_available_skills(messages)
        assert "<available_skills>" in enriched[0]["content"]
        assert "<available_skills>" not in messages[0]["content"]

    default_loader = SkillLoader.from_defaults()
    assert any(skill.name == "route-and-answer" for skill in default_loader.discover())
    context = build_model_context(new_messages(), tools, skill_loader=default_loader)
    assert "<available_skills>" in context.system_messages[0]["content"]
    print("skill_loader_smoke=passed")


if __name__ == "__main__":
    main()
