"""Real isolated folders verify version identity, full assets and legacy recovery."""
from __future__ import annotations

import io
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image

from creatoros.integrations.producer_skills import ProducerSkillCatalog, SkillDigestConflict, _digest


def image_bytes(color="blue"):
    stream = io.BytesIO()
    Image.new("RGB", (16, 16), color).save(stream, format="PNG")
    return stream.getvalue()


class SkillVersionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.catalog = ProducerSkillCatalog(self.root / "catalog")
        source = self.root / "source"
        (source / "assets").mkdir(parents=True)
        (source / "scripts").mkdir()
        (source / "SKILL.md").write_text(
            "---\nname: version-test\ndescription: old description\n"
            "creatoros-output: social-content-pack.image-carousel\n---\n"
            "Use [reference](assets/reference.png).\n", encoding="utf-8")
        (source / "assets/reference.png").write_bytes(image_bytes())
        (source / "assets/unused.png").write_bytes(image_bytes("yellow"))
        (source / "scripts/build.py").write_text("print('old')\n", encoding="utf-8")
        self.skill = self.catalog.register_local(source, role="production")
        self.skill_id = self.skill["id"]
        self.initial = self.skill["digest"]
        self.working = Path(self.skill["local_path"])
        self.original = self.catalog.root / "versions" / self.skill_id
        self.original_bytes = self.tree(self.original)
        self.working_bytes = self.tree(self.working)

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def tree(directory):
        return {path.relative_to(directory).as_posix(): path.read_bytes()
                for path in directory.rglob("*") if path.is_file()}

    def candidate(self, request_id="candidate-1"):
        return self.catalog.create_skill_candidate(self.skill_id, self.initial, request_id)

    def test_candidate_is_a_full_independent_copy_and_hidden_from_library(self):
        candidate = self.candidate()
        self.assertNotEqual(candidate["id"], self.skill_id)
        self.assertEqual(candidate["family_id"], self.skill_id)
        self.assertEqual(candidate["parent_id"], self.skill_id)
        self.assertEqual(candidate["version_number"], 2)
        self.assertEqual(candidate["version_status"], "candidate")
        self.assertEqual(self.tree(Path(candidate["local_path"])), self.working_bytes)
        self.assertEqual([item["id"] for item in self.catalog.list() if item["id"] != self.catalog.BUILTIN_ID],
                         [self.skill_id])
        with self.assertRaisesRegex(ValueError, "候选"):
            self.catalog.resolve(candidate["id"])
        self.assertEqual(self.catalog.locate(candidate["id"]), Path(candidate["local_path"]))

    def test_text_edit_on_released_creates_candidate_without_changing_old_bytes(self):
        body = self.catalog.read_skill_file(self.skill_id, "SKILL.md")["content"]
        result = self.catalog.update_skill_file(self.skill_id, "SKILL.md", body.replace("old description", "new description"), self.initial)
        self.assertEqual(result["version_status"], "candidate")
        self.assertNotEqual(result["skill_id"], self.skill_id)
        self.assertEqual(self.catalog.describe(result["skill_id"])["description"], "new description")
        self.assertEqual(self.catalog.describe(self.skill_id)["description"], "old description")
        self.assertEqual(self.tree(self.working), self.working_bytes)
        self.assertEqual(self.tree(self.original), self.original_bytes)
        self.assertEqual((self.catalog.locate(result["skill_id"]) / "scripts/build.py").read_bytes(), self.working_bytes["scripts/build.py"])

    def test_candidate_edits_stay_in_candidate_and_stale_digest_cannot_overwrite(self):
        candidate = self.candidate()
        result = self.catalog.update_skill_file(candidate["id"], "scripts/build.py", "print('new')\n", candidate["digest"])
        self.assertEqual(result["skill_id"], candidate["id"])
        with self.assertRaises(SkillDigestConflict):
            self.catalog.update_skill_file(candidate["id"], "scripts/build.py", "lost edit", candidate["digest"])
        self.assertEqual(self.catalog.read_skill_file(candidate["id"], "scripts/build.py")["content"], "print('new')\n")
        self.assertEqual(self.tree(self.working), self.working_bytes)

    def test_image_add_replace_delete_are_all_candidate_only(self):
        added = self.catalog.save_skill_image(self.skill_id, "assets/new.png", image_bytes("red"), self.initial)
        candidate_id = added["skill_id"]
        replaced = self.catalog.save_skill_image(candidate_id, "assets/reference.png", image_bytes("green"), added["digest"], overwrite=True)
        removed = self.catalog.delete_skill_image(candidate_id, "assets/unused.png", replaced["digest"])
        candidate = self.catalog.locate(candidate_id)
        self.assertEqual((candidate / "assets/new.png").read_bytes(), image_bytes("red"))
        self.assertEqual((candidate / "assets/reference.png").read_bytes(), image_bytes("green"))
        self.assertFalse((candidate / "assets/unused.png").exists())
        self.assertEqual(removed["version_status"], "candidate")
        self.assertEqual(self.tree(self.working), self.working_bytes)
        self.assertEqual(self.tree(self.original), self.original_bytes)

    def test_invalid_edit_and_asset_conflict_do_not_create_candidates(self):
        before = len(self.catalog.list_skill_versions(self.skill_id)["items"])
        with self.assertRaises(ValueError):
            self.catalog.update_skill_file(self.skill_id, "SKILL.md", "bad frontmatter", self.initial)
        with self.assertRaises(ValueError):
            self.catalog.save_skill_image(self.skill_id, "assets/invalid.png", b"not-image", self.initial)
        with self.assertRaises(ValueError):
            self.catalog.delete_skill_image(self.skill_id, "assets/reference.png", self.initial)
        self.assertEqual(len(self.catalog.list_skill_versions(self.skill_id)["items"]), before)
        self.assertEqual(self.tree(self.working), self.working_bytes)

    def test_candidate_request_replay_is_persistent_and_conflicting_input_is_rejected(self):
        candidate = self.candidate()
        changed = self.catalog.update_skill_file(candidate["id"], "scripts/build.py", "candidate body", candidate["digest"])
        restarted = ProducerSkillCatalog(self.catalog.root)
        replay = restarted.create_skill_candidate(self.skill_id, self.initial, "candidate-1")
        self.assertEqual(replay["id"], candidate["id"])
        self.assertEqual(replay["digest"], changed["digest"])
        with self.assertRaisesRegex(ValueError, "输入已改变"):
            restarted.create_skill_candidate(self.skill_id, "f" * 64, "candidate-1")
        self.assertEqual(len(restarted.list_skill_versions(candidate["id"])["items"]), 2)

    def test_direct_editor_response_retry_reuses_same_candidate(self):
        result = self.catalog.update_skill_file(self.skill_id, "scripts/build.py", "new body", self.initial)
        replay = self.catalog.update_skill_file(self.skill_id, "scripts/build.py", "new body", self.initial)
        self.assertEqual(result["skill_id"], replay["skill_id"])
        self.assertEqual(result["digest"], replay["digest"])
        self.assertEqual(len(self.catalog.list_skill_versions(self.skill_id)["items"]), 2)

    def test_noop_edit_retry_after_publication_never_rewrites_released_files(self):
        content = self.catalog.read_skill_file(self.skill_id, "scripts/build.py")["content"]
        first = self.catalog.update_skill_file(self.skill_id, "scripts/build.py", content, self.initial)
        self.catalog.publish_skill_candidate(first["skill_id"], first["digest"])
        with patch.object(self.catalog, "_update_skill_file_locked", side_effect=AssertionError("must not write released version")):
            replay = self.catalog.update_skill_file(self.skill_id, "scripts/build.py", content, self.initial)
        self.assertEqual(replay["skill_id"], first["skill_id"])
        self.assertEqual(replay["version_status"], "released")
        self.assertEqual(replay["digest"], first["digest"])

    def test_publish_has_final_full_original_and_subsequent_edits_fork_again(self):
        candidate = self.candidate()
        changed = self.catalog.save_skill_image(candidate["id"], "assets/reference.png", image_bytes("red"), candidate["digest"], overwrite=True)
        with self.assertRaises(SkillDigestConflict):
            self.catalog.publish_skill_candidate(candidate["id"], candidate["digest"])
        released = self.catalog.publish_skill_candidate(candidate["id"], changed["digest"])
        self.assertEqual(released["version_status"], "released")
        self.assertEqual(_digest(self.catalog.original(released["id"], released["digest"])), released["digest"])
        self.assertEqual(self.catalog.resolve(released["id"]), Path(released["local_path"]))
        self.assertEqual(self.catalog.publish_skill_candidate(released["id"], released["digest"])["id"], released["id"])
        latest = [item for item in self.catalog.list() if item["id"] != self.catalog.BUILTIN_ID]
        self.assertEqual([item["id"] for item in latest], [released["id"]])
        next_edit = self.catalog.update_skill_file(released["id"], "scripts/build.py", "v3", released["digest"])
        self.assertEqual(next_edit["version_number"], 3)
        self.assertNotEqual(next_edit["skill_id"], released["id"])
        self.assertEqual(self.catalog.read_skill_file(released["id"], "scripts/build.py")["content"].strip(), "print('old')")
        self.assertEqual(self.tree(self.original), self.original_bytes)

    def test_publish_registry_failure_is_recoverable_without_losing_old_or_new_files(self):
        candidate = self.candidate()
        changed = self.catalog.update_skill_file(candidate["id"], "scripts/build.py", "new publication", candidate["digest"])
        with patch("creatoros.integrations.skill_versions._write_record", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.catalog.publish_skill_candidate(candidate["id"], changed["digest"])
        restarted = ProducerSkillCatalog(self.catalog.root)
        self.assertEqual(restarted.describe(candidate["id"])["version_status"], "candidate")
        recovered = restarted.publish_skill_candidate(candidate["id"], changed["digest"])
        self.assertEqual(recovered["version_status"], "released")
        self.assertEqual(self.tree(self.working), self.working_bytes)

    def test_failed_publication_can_be_edited_then_published_again(self):
        candidate = self.candidate()
        changed = self.catalog.update_skill_file(candidate["id"], "scripts/build.py", "first candidate", candidate["digest"])
        with patch("creatoros.integrations.skill_versions._write_record", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.catalog.publish_skill_candidate(candidate["id"], changed["digest"])
        changed_again = self.catalog.update_skill_file(candidate["id"], "scripts/build.py", "second candidate", changed["digest"])
        published = self.catalog.publish_skill_candidate(candidate["id"], changed_again["digest"])
        self.assertEqual(_digest(self.catalog.original(published["id"], published["digest"])), changed_again["digest"])
        self.assertEqual(self.tree(self.working), self.working_bytes)

    def test_legacy_changed_working_baseline_and_import_are_both_recoverable(self):
        # Historical CreatorOS allowed in-place working edits, so imported v0
        # and current released v1 can legitimately be different full folders.
        (self.working / "scripts/build.py").write_text("legacy newer body", encoding="utf-8")
        current_digest = _digest(self.working)
        self.catalog.create_skill_candidate(self.skill_id, current_digest, "legacy-baseline")
        self.assertEqual(self.tree(self.original), self.original_bytes)
        self.assertEqual(_digest(self.catalog.original(self.skill_id, self.initial)), self.initial)
        self.assertEqual(_digest(self.catalog.original(self.skill_id, current_digest)), current_digest)
        shutil.rmtree(self.working)
        restored = ProducerSkillCatalog(self.catalog.root).describe(self.skill_id)
        self.assertEqual(restored["digest"], current_digest)
        self.assertEqual((Path(restored["local_path"]) / "scripts/build.py").read_text(encoding="utf-8"), "legacy newer body")

    def test_full_draft_import_preserves_assets_scripts_and_rejects_changed_request(self):
        draft = self.root / "trial-draft"
        shutil.copytree(self.working, draft)
        (draft / "assets/reference.png").write_bytes(image_bytes("green"))
        (draft / "scripts/build.py").write_text("full draft", encoding="utf-8")
        candidate = self.catalog.create_skill_candidate_from_directory(self.skill_id, self.initial, draft, "draft-request")
        self.assertEqual(self.tree(Path(candidate["local_path"])), self.tree(draft))
        self.assertEqual(candidate["parent_digest"], self.initial)
        (draft / "scripts/build.py").write_text("changed request", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "输入已改变"):
            self.catalog.create_skill_candidate_from_directory(self.skill_id, self.initial, draft, "draft-request")
        self.assertEqual(self.tree(self.working), self.working_bytes)

    def test_release_rejects_out_of_band_mutation(self):
        candidate = self.candidate()
        released = self.catalog.publish_skill_candidate(candidate["id"], candidate["digest"])
        Path(released["local_path"]).joinpath("scripts/build.py").write_text("external mutation", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "已发布 Skill"):
            self.catalog.describe(released["id"])
        self.assertEqual(_digest(self.catalog.original(released["id"], released["digest"])), released["digest"])

    def test_pair_snapshot_rejects_candidate_even_if_caller_manually_bound_it(self):
        from creatoros.integrations.skill_pair import snapshot_pair
        mind_source = self.root / "mind-source"
        shutil.copytree(self.working, mind_source)
        mind = self.catalog.register_local(mind_source, role="mind")
        mind_candidate = self.catalog.create_skill_candidate(mind["id"], mind["digest"], "mind-candidate")
        production_candidate = self.candidate()
        snapshot_pair(self.catalog, mind["id"], self.skill_id, native=True)
        for mind_id, production_id in ((mind_candidate["id"], self.skill_id),
                                       (mind["id"], production_candidate["id"])):
            with self.subTest(mind=mind_id), self.assertRaisesRegex(ValueError, "候选"):
                snapshot_pair(self.catalog, mind_id, production_id, native=True)


if __name__ == "__main__":
    unittest.main()
