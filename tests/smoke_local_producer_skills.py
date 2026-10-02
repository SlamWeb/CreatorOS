"""Isolated local Skill import/replay checks; no model or external API calls."""
import os
import re
from pathlib import Path
from tempfile import TemporaryDirectory

from creatoros.integrations.producer_skills import ProducerSkillCatalog


def write_skill(directory: Path, *, name: str, description: str, compatible: bool = False):
    directory.mkdir(parents=True, exist_ok=True)
    contract = "creatoros-output: social-content-pack.image-carousel\n" if compatible else ""
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{contract}---\n\nSkill instructions.\n",
        encoding="utf-8",
    )


def expect_rejected(action):
    try:
        action()
    except ValueError:
        return
    raise AssertionError("invalid local Skill was accepted")


def main():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        catalog = ProducerSkillCatalog(root / "catalog")

        source = root / "authoring" / "plain-skill"
        write_skill(source, name="local-scenes", description="A local image carousel skill", compatible=True)
        (source / "references").mkdir()
        (source / "references" / "example.md").write_text("local reference", encoding="utf-8")
        original_bytes = (source / "SKILL.md").read_bytes()
        registered = catalog.register_local(source, role="production", source_note="authored locally")
        skill_id = registered["id"]
        assert re.fullmatch(r"local-scenes--[a-f0-9]{16}", skill_id)
        assert registered["source_kind"] == "local"
        assert registered["source_note"] == "authored locally"
        assert registered["github_url"] is None and registered["commit"] is None
        assert registered["role"] == "production" and registered["producible"] is True
        assert Path(registered["local_path"]) == catalog.root / "working" / skill_id
        assert (catalog.root / "versions" / skill_id / "SKILL.md").read_bytes() == original_bytes

        working_skill = Path(registered["local_path"]) / "SKILL.md"
        working_skill.write_text(working_skill.read_text(encoding="utf-8") + "\nlocal edit\n", encoding="utf-8")
        replayed = catalog.register_local(source, role="production", source_note="updated note")
        assert replayed["id"] == skill_id
        assert "local edit" in working_skill.read_text(encoding="utf-8")
        assert (catalog.root / "versions" / skill_id / "SKILL.md").read_bytes() == original_bytes

        (source / "SKILL.md").write_text(
            (source / "SKILL.md").read_text(encoding="utf-8").replace(
                "A local image carousel skill", "A revised local image carousel skill"),
            encoding="utf-8",
        )
        revised_source_bytes = (source / "SKILL.md").read_bytes()
        revised = catalog.register_local(source, role="production")
        assert revised["id"] != skill_id
        assert (catalog.root / "versions" / revised["id"] / "SKILL.md").read_bytes() == revised_source_bytes
        assert "local edit" in working_skill.read_text(encoding="utf-8")

        mind_source = root / "authoring" / "mind-skill"
        write_skill(mind_source, name="local-mind", description="A local content skill")
        mind = catalog.register_local(mind_source, role="mind")
        assert mind["source_kind"] == "local" and mind["role"] == "mind"
        assert mind["producible"] is False and mind["github_url"] is None and mind["commit"] is None
        expect_rejected(lambda: catalog.resolve(mind["id"]))

        malformed = root / "authoring" / "malformed"
        malformed.mkdir()
        (malformed / "SKILL.md").write_text("---\nname: Invalid Name\ndescription: missing valid name\n---\n", encoding="utf-8")
        expect_rejected(lambda: catalog.register_local(malformed, role="mind"))

        linked = root / "authoring" / "linked-resource"
        write_skill(linked, name="linked-resource", description="Reject linked resources")
        outside = root / "outside.md"
        outside.write_text("outside", encoding="utf-8")
        try:
            os.symlink(outside, linked / "reference.md")
        except OSError as error:
            raise AssertionError(f"test environment cannot create a symlink: {error}") from error
        expect_rejected(lambda: catalog.register_local(linked, role="mind"))

        assert len(list((catalog.root / "registry").glob("*.json"))) == 3
    print("local_producer_skills_smoke=passed import=plain+mind replay=working-preserved content-versioned invalid=blocked symlink=blocked model_calls=0")


if __name__ == "__main__":
    main()
