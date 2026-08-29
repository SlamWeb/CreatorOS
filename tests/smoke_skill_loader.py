from pathlib import Path
from tempfile import TemporaryDirectory

from creatoros.agent.loop import build_model_context
from creatoros.session.snapshot import new_messages
from creatoros.skills.loader import SkillLoader
from creatoros.tools.definitions import tools


def write_skill(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main():
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
