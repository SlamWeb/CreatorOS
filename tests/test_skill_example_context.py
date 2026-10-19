"""Local source-context and isolation tests, not paid research/story-quality Eval."""
import hashlib
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

from creatoros.integrations.native_production import delivery_prompt
from creatoros.integrations.producer_skills import ProducerSkillCatalog
from creatoros.integrations.skill_examples import linked_skill_examples
from creatoros.integrations.topic_research import CodexTopicResearcher, TopicResearchService
from creatoros.storage import Database, Series, upgrade_database


class SkillExampleContextTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="skill-example-context-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_full_text_with_relative_source_digest_and_one_nested_reference(self):
        text = "开场的蛋糕，结尾送给朋友。\n[逐镜头](shots.md)"
        first = self.write("references/fewshots.md", text)
        self.write("references/shots.md", "拿出两块蛋糕，而不是抽象鸡汤。")
        self.write("references/unlinked.md", "DO_NOT_INCLUDE")
        snapshot = linked_skill_examples(self.root, "[完整故事](references/fewshots.md)")
        self.assertEqual([f["path"] for f in snapshot["files"]],
                         ["references/fewshots.md", "references/shots.md"])
        self.assertEqual(snapshot["files"][0]["text"], first.read_bytes().decode("utf-8"))
        self.assertEqual(snapshot["files"][0]["sha256"], hashlib.sha256(first.read_bytes()).hexdigest())
        self.assertNotIn("DO_NOT_INCLUDE", json.dumps(snapshot))

    def test_backticks_encoded_spaces_and_duplicates(self):
        self.write("examples/a story.md", "完整原文")
        snapshot = linked_skill_examples(self.root,
            "[story](examples/a%20story.md#part) and `examples/a story.md`")
        self.assertEqual(len(snapshot["files"]), 1)
        self.assertEqual(snapshot["files"][0]["path"], "examples/a story.md")

    def test_no_cross_skill_hidden_credentials_or_scripts(self):
        self.write("assets/credentials.md", "SECRET")
        self.write("scripts/helper.md", "SCRIPT")
        self.write("references/.env.md", "ENV")
        snapshot = linked_skill_examples(self.root, "[x](../other/SKILL.md) "
            "[x](assets/credentials.md) [x](scripts/helper.md) [x](references/.env.md)")
        self.assertFalse(snapshot["files"])
        self.assertEqual(len(snapshot["omitted"]), 4)
        self.assertNotIn("SECRET", json.dumps(snapshot))

    def test_windows_ads_devices_and_ambiguous_suffixes_rejected_before_open(self):
        targets = ["references/a.md:private.md", "references/NUL.md", "references/CON.md",
                   "references/COM1.md", "references/LPT¹.md", "references/CON .md",
                   "references/dir./story.md", "references/dir /story.md"]
        with patch.object(Path, "open", side_effect=AssertionError("must reject before opening")):
            result = linked_skill_examples(self.root, " ".join(f"[sample]({target})" for target in targets[:5])
                + " " + " ".join(f"[sample](<{target}>)" for target in targets[5:]))
        self.assertFalse(result["files"])
        self.assertEqual(len(result["omitted"]), len(targets))
        self.assertTrue(all(item["reason"] == "unsafe_path" for item in result["omitted"]))

    def test_directories_are_not_opened_as_text_files(self):
        (self.root / "references/folder.md").mkdir(parents=True)
        with patch.object(Path, "open", side_effect=AssertionError("must not open directory")):
            result = linked_skill_examples(self.root, "[sample](references/folder.md)")
        self.assertEqual(result["omitted"][0]["reason"], "not_regular_file")

    def test_hidden_or_reparse_attributes_are_not_read(self):
        sample = self.write("references/example.md", "PRIVATE_DATA")
        original_stat = Path.stat

        def file_stat(path, *args, **kwargs):
            value = original_stat(path, *args, **kwargs)
            if path == sample:
                return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=2)
            return value

        with patch("creatoros.integrations.skill_examples.stat.FILE_ATTRIBUTE_HIDDEN", 2, create=True), \
                patch.object(Path, "stat", file_stat):
            result = linked_skill_examples(self.root, "[sample](references/example.md)")
        self.assertFalse(result["files"])
        self.assertEqual(result["omitted"][0]["reason"], "hidden_or_reparse_path")

    def test_image_missing_text_and_remote_are_not_read_or_fetched(self):
        snapshot = linked_skill_examples(self.root,
            "![example](assets/one.png) [missing](references/missing.md) [web](https://example.com/a.md)")
        self.assertFalse(snapshot["files"])
        self.assertEqual([x["reason"] for x in snapshot["omitted"]],
                         ["image_not_loaded_in_research", "unreadable_text"])

    def test_size_budget_does_not_silently_truncate(self):
        self.write("examples/large.md", "x" * 33)
        self.write("examples/ok.md", "完整故事")
        with patch("creatoros.integrations.skill_examples.MAX_FILE_BYTES", 32):
            snapshot = linked_skill_examples(self.root, "[a](examples/large.md) [b](examples/ok.md)")
        self.assertEqual(snapshot["omitted"][0]["reason"], "file_size_limit")
        self.assertEqual(snapshot["files"][0]["text"], "完整故事")
        with patch("creatoros.integrations.skill_examples.MAX_TOTAL_BYTES", 1):
            self.assertEqual(linked_skill_examples(self.root, "[b](examples/ok.md)")["omitted"][0]["reason"],
                             "total_size_limit")

    def test_bounded_depth_and_count(self):
        self.write("examples/a.md", "[b](b.md)")
        self.write("examples/b.md", "[c](c.md)")
        self.write("examples/c.md", "TOO_DEEP")
        snapshot = linked_skill_examples(self.root, "[a](examples/a.md)")
        self.assertEqual(snapshot["omitted"][0]["reason"], "depth_limit")
        with patch("creatoros.integrations.skill_examples.MAX_FILES", 1):
            self.assertEqual(linked_skill_examples(self.root, "[a](examples/a.md)")["omitted"][0]["reason"],
                             "file_limit")

    def test_linked_file_is_not_followed(self):
        source = self.write("private.md", "DO_NOT_INCLUDE")
        link = self.root / "references/linked.md"
        link.parent.mkdir()
        try:
            link.symlink_to(source)
        except OSError:
            self.skipTest("This Windows user cannot create symlinks")
        snapshot = linked_skill_examples(self.root, "[example](references/linked.md)")
        self.assertFalse(snapshot["files"])
        self.assertEqual(snapshot["omitted"][0]["reason"], "linked_path")

    def test_series_snapshot_uses_mind_examples_and_freezes_text_before_later_edit(self):
        skill_root = self.root / "source-mind"
        skill_root.mkdir()
        (skill_root / "SKILL.md").write_text(
            "---\nname: story-mind\ndescription: Story examples\n---\n[故事](references/fewshots.md)", encoding="utf-8")
        (skill_root / "references").mkdir()
        (skill_root / "references/fewshots.md").write_text("FIRST_ORIGINAL_STORY", encoding="utf-8")
        visual = self.root / "visual"
        visual.mkdir()
        (visual / "SKILL.md").write_text(
            "---\nname: story-visual\ndescription: Visual only\n---\nVISUAL_ONLY", encoding="utf-8")
        catalog = ProducerSkillCatalog(self.root / "catalog")
        mind = catalog.register_local(skill_root, role="mind")
        maker = catalog.register_local(visual, role="production")
        url = f"sqlite:///{(self.root / 'test.db').as_posix()}"
        upgrade_database(url)
        db = Database(url)
        self.addCleanup(db.close)
        with db.session() as session:
            session.add(Series(id="s", creator_id=None, name="Story", description="", audience="",
                               skill_name=None, mind_skill_id=mind["id"], production_skill_id=maker["id"]))
        snapshot = TopicResearchService(db, catalog).snapshot("s")
        self.assertEqual(snapshot["skill_examples"]["files"][0]["text"], "FIRST_ORIGINAL_STORY")
        self.assertNotIn("VISUAL_ONLY", json.dumps(snapshot))
        (catalog.locate(mind["id"]) / "references/fewshots.md").write_text("NEW_STORY", encoding="utf-8")
        self.assertEqual(snapshot["skill_examples"]["files"][0]["text"], "FIRST_ORIGINAL_STORY")

    def test_research_prompt_contains_full_sample_without_reopening_shared_directory(self):
        researcher = CodexTopicResearcher()
        workspace = self.root / "work"
        workspace.mkdir()
        captured = []

        async def capture(prompt, *args, **kwargs):
            captured.append(prompt)
            return "local-conversion-test"

        snapshot = {"skill_directory": "do-not-read", "skill_text": "故事方法",
                    "skill_examples": {"files": [{"path": "references/fewshots.md", "text": "FULL_STORY"}],
                                       "omitted": [{"path": "assets/a.png", "reason": "image_not_loaded_in_research"}]}}
        with patch.object(researcher, "preflight"), patch.object(researcher, "_research_async", side_effect=capture):
            result = researcher.research(snapshot, 3, "参考例子", workspace, Event())
        self.assertEqual(result, "local-conversion-test")
        self.assertIn("FULL_STORY", captured[0])
        self.assertIn("不得声称已看原图", captured[0])
        self.assertIn("不自行读取 skill_directory", captured[0])
        self.assertEqual((workspace / "research_request.txt").read_text(encoding="utf-8"), captured[0])

    def test_production_uses_both_original_refs_and_actual_visual_reference_instruction(self):
        mind = self.write("mind/SKILL.md", "Original mind")
        visual = self.write("visual/SKILL.md", "Original visual")
        prompt = delivery_prompt(self.root, [("mind", mind), ("visual", visual)], "新的故事")
        self.assertIn(str(mind.resolve()), prompt)
        self.assertIn(str(visual.resolve()), prompt)
        self.assertIn("不要先另写一份融合或摘要 Skill 代替原件", prompt)
        self.assertIn("参考图传入生图工具", prompt)


if __name__ == "__main__":
    unittest.main()
