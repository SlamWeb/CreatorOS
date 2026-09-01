from creatoros.skills.loader import SkillLoader


def main():
    loader = SkillLoader.from_defaults()
    discovered = {skill.name: skill for skill in loader.discover()}
    assert "knowledge-to-carousel" in discovered

    skill = loader.load("knowledge-to-carousel")
    assert skill.content is not None
    assert "big pictures, few words" in skill.content
    assert "social_content_pack.json" in skill.content
    assert "Do not return an HTML page" in skill.content
    assert (skill.path.parent / "references" / "social-content-pack.md").is_file()
    print("knowledge_to_carousel_skill_smoke=passed")


if __name__ == "__main__":
    main()
