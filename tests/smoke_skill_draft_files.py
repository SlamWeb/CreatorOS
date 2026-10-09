"""Native Skill draft folder parsing, preservation and path-boundary smoke."""
import asyncio
import base64
import sys
import threading
from contextlib import asynccontextmanager
from types import ModuleType, SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

from creatoros.integrations.producer_skills import ProducerSkillCatalog
from creatoros.integrations.skill_draft_files import (
    MODE_FOLDERS, read_file_drafts, seed_file_drafts,
)
from creatoros.integrations.skill_extraction import (
    MODE_ROLES, ExtractionError, SkillExtractionService,
)


def skill_text(name="sample-skill"):
    return (f"---\nname: {name}\ndescription: Reusable method from the sample.\n---\n\n"
            "Use the method with a new topic.\n")


def write_native_drafts(directory, mode, *, output_kind="image-carousel", resource=b"support resource"):
    for index, (role, folder) in enumerate(zip(MODE_ROLES[mode], MODE_FOLDERS[mode]), 1):
        target = directory / "draft" / folder
        target.mkdir(parents=True, exist_ok=True)
        marker = "creatoros-output: text\n" if output_kind == "text" else ""
        (target / "SKILL.md").write_text(
            skill_text(f"{role.replace('_', '-')}-skill").replace("description:", marker + "description:"),
            encoding="utf-8")
        (target / "references").mkdir()
        (target / "references" / f"guide-{index}.md").write_bytes(resource)


def main():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)

        # Each selected mode maps its native directory slots to the expected DTO roles.
        pair = root / "pair-job"
        write_native_drafts(pair, "pair")
        parsed = read_file_drafts(pair, "pair", "受控响应")
        assert [item.role for item in parsed.skills] == MODE_ROLES["pair"]
        assert [item.name for item in parsed.skills] == ["mind-skill", "production-skill"]
        assert parsed._directories == {
            role: pair / "draft" / folder
            for role, folder in zip(MODE_ROLES["pair"], MODE_FOLDERS["pair"])
        }
        assert (parsed._directories["production"] / "references" / "guide-2.md").read_bytes() == b"support resource"

        # A missing or malformed SKILL.md cannot become a ready draft.
        missing = root / "missing-job"
        (missing / "draft" / "skill").mkdir(parents=True)
        try:
            read_file_drafts(missing, "single", "missing")
            raise AssertionError("missing SKILL.md was accepted")
        except ExtractionError:
            pass
        invalid = root / "invalid-job"
        (invalid / "draft" / "skill").mkdir(parents=True)
        (invalid / "draft" / "skill" / "SKILL.md").write_text("not frontmatter", encoding="utf-8")
        try:
            read_file_drafts(invalid, "single", "invalid")
            raise AssertionError("invalid SKILL.md was accepted")
        except ExtractionError:
            pass

        # Text-only targets inherit text capability when no explicit declaration exists.
        text_job = root / "text-job"
        write_native_drafts(text_job, "single")
        text_result = read_file_drafts(text_job, "single", "text", default_kind="text")
        assert text_result.skills[0].output_kind == "text"

        # Revision staging copies complete old Skill trees and leaves their source intact.
        source = root / "old" / "production"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(skill_text("old-production"), encoding="utf-8")
        (source / "references").mkdir()
        (source / "references" / "guide.md").write_bytes(b"immutable guide")
        revision = root / "revision-job"
        seed_file_drafts(revision, "visual", {"production": source})
        staged = revision / "draft" / MODE_FOLDERS["visual"][0]
        assert (staged / "SKILL.md").read_text(encoding="utf-8") == skill_text("old-production")
        assert (staged / "references" / "guide.md").read_bytes() == b"immutable guide"
        assert (source / "references" / "guide.md").read_bytes() == b"immutable guide"
        assert (source / "SKILL.md").read_text(encoding="utf-8") == skill_text("old-production")

        # SDK wiring writes into the real draft tree using text plus selected image inputs.
        from creatoros.integrations import skill_extraction
        sdk_root = root / "sdk-job"
        sdk_root.mkdir()
        sdk_image = root / "sdk-reference.png"
        Image.new("RGB", (8, 8), "white").save(sdk_image, format="PNG")
        sdk_calls = {}

        class Input:
            def __init__(self, value):
                self.value = value

        class FakeThread:
            id = "sdk-thread"

            async def turn(self, inputs, **controls):
                sdk_calls["inputs"] = inputs
                sdk_calls["turn_controls"] = controls
                write_native_drafts(sdk_root, "single", resource=b"written during SDK turn")
                return object()

        class FakeClient:
            async def thread_start(self, **controls):
                sdk_calls["thread_controls"] = controls
                return FakeThread()

        @asynccontextmanager
        async def fake_client(_deadline, _cancel):
            yield FakeClient()

        sdk = ModuleType("openai_codex")
        sdk.ApprovalMode = SimpleNamespace(deny_all="deny_all")
        sdk.LocalImageInput = type("LocalImageInput", (Input,), {})
        sdk.Sandbox = SimpleNamespace(workspace_write="workspace_write")
        sdk.TextInput = type("TextInput", (Input,), {})
        sdk_calls["thread_id"] = None
        with patch.dict(sys.modules, {"openai_codex": sdk}), \
             patch.object(skill_extraction, "_production_client", fake_client), \
             patch.object(skill_extraction, "collect_observed_turn", new=lambda *_: _completed_turn()):
            sdk_result = asyncio.run(skill_extraction.sdk_extract(
                sdk_root, [sdk_image], "single", "extract reusable method", threading.Event(),
                lambda value: sdk_calls.__setitem__("thread_id", value)))
        assert sdk_calls["thread_id"] == "sdk-thread"
        assert sdk_calls["thread_controls"]["cwd"] == str((sdk_root / "draft").resolve())
        assert sdk_calls["thread_controls"]["sandbox"] == "workspace_write"
        assert sdk_calls["turn_controls"]["sandbox"] == "workspace_write"
        assert "output_schema" not in sdk_calls["thread_controls"]
        assert "output_schema" not in sdk_calls["turn_controls"]
        assert [type(item).__name__ for item in sdk_calls["inputs"]] == ["TextInput", "LocalImageInput"]
        assert sdk_result.note == "说明来自最终响应，不是 JSON"
        assert sdk_result.skills[0].name == "legacy-end-to-end-skill"
        assert sdk_result._directories["legacy_end_to_end"] == sdk_root / "draft" / "skill"
        assert "说明来自最终响应" not in sdk_result.skills[0].skill_md
        assert (sdk_root / "instructions.txt").is_file()
        assert "SKILL.md" in (sdk_root / "instructions.txt").read_text(encoding="utf-8")

        # Links and resolved paths outside the selected native Skill folder are rejected.
        linked = root / "linked-job"
        write_native_drafts(linked, "single")
        outside = root / "outside.md"
        outside.write_text("must not be read", encoding="utf-8")
        link = linked / "draft" / "skill" / "outside.md"
        try:
            link.symlink_to(outside)
        except OSError as error:
            raise AssertionError(f"symlink boundary smoke requires symlink support: {error}")
        try:
            read_file_drafts(linked, "single", "linked")
            raise AssertionError("linked out-of-root file was accepted")
        except ExtractionError:
            pass

        # Published, edited and saved copies retain model-authored support files.
        catalog = ProducerSkillCatalog(root / "catalog")
        image_path = root / "reference.png"
        Image.new("RGB", (8, 8), "white").save(image_path, format="PNG")

        async def extractor(directory, images, mode, instruction, cancel, on_thread):
            write_native_drafts(directory, mode, resource=b"carry through publish/edit/save")
            for role, folder in zip(MODE_ROLES[mode], MODE_FOLDERS[mode]):
                if role != "mind":
                    assets = directory / "draft" / folder / "assets"
                    assets.mkdir(exist_ok=True)
                    for image in images:
                        (assets / image.name).write_bytes(image.read_bytes())
            return read_file_drafts(directory, mode, "native files parsed")

        service = SkillExtractionService(catalog, extractor=extractor)
        upload = service.upload("reference.png", base64.b64encode(image_path.read_bytes()).decode())
        job = service.submit("native-files", [upload["id"]], mode="single")
        service.worker.join(5)
        ready = service.get(job["id"])
        assert ready["status"] == "ready", ready
        native_role = ready["skills"][0]["role"]
        draft_root = service._draft_root(ready)
        resource_rel = Path("references") / "guide-1.md"
        assert (draft_root / native_role / resource_rel).read_bytes() == b"carry through publish/edit/save"
        assert any(f["path"] == resource_rel.as_posix() for f in ready["files"])
        assert service.file(job["id"], native_role, resource_rel.as_posix()).read_bytes() == b"carry through publish/edit/save"

        edited_md = ready["skills"][0]["skill_md"] + "\nOne user edit.\n"
        edited = service.edit(job["id"], ready["digest"], [{
            **ready["skills"][0], "skill_md": edited_md,
        }])
        assert edited["status"] == "ready"
        assert (service._draft_root(edited) / native_role / resource_rel).read_bytes() == b"carry through publish/edit/save"
        saved = service.save(job["id"], edited["digest"])
        assert saved["status"] == "saved", saved
        installed = Path(saved["saved_skills"][0]["local_path"])
        assert (installed / resource_rel).read_bytes() == b"carry through publish/edit/save"
        service.shutdown()

    print("skill_draft_files_smoke=passed native-slots/validation/text-default/resources/revision-copy/path-boundary")


async def _completed_turn():
    return SimpleNamespace(status="completed", final_response="说明来自最终响应，不是 JSON", usage=None)


if __name__ == "__main__":
    main()
