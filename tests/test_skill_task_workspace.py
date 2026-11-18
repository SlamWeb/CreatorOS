import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from creatoros.integrations.producer_skills import _digest
from creatoros.integrations.skill_task_workspace import SkillTaskInput, SkillTaskSkill, SkillTaskWorkspaceStore


class SkillTaskWorkspaceTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = SkillTaskWorkspaceStore(self.root / "sessions")
        self.skill = self.root / "catalog" / "sample"
        (self.skill / "references").mkdir(parents=True)
        (self.skill / "SKILL.md").write_text("Read references.", encoding="utf-8")
        (self.skill / "references" / "叙事.md").write_text("通过画面推动故事。", encoding="utf-8")
        (self.skill / ".env").write_text("PRIVATE=fixture", encoding="utf-8")
        self.attachment = self.root / "selected-input"
        self.attachment.write_bytes(b"an attached file")
        self.selected_skill = SkillTaskSkill("sample-skill", self.skill, _digest(self.skill))
        self.selected_input = SkillTaskInput("用户素材.txt", self.attachment,
                                              hashlib.sha256(self.attachment.read_bytes()).hexdigest())

    def prepare(self, session="session-a", task="task-a"):
        return self.store.create(session, task, skills=[self.selected_skill], inputs=[self.selected_input])

    def test_freezes_version_and_attachment_without_private_skill_files(self):
        workspace = self.prepare()
        skill_path, = workspace.skill_files
        input_path, = workspace.input_files
        self.assertEqual(workspace.read_text(skill_path), "Read references.")
        self.assertEqual(workspace.read_text(input_path), "an attached file")
        reference = "skills/sample-skill/references/叙事.md"
        self.assertEqual(workspace.read_text(reference), "通过画面推动故事。")
        self.assertEqual({item["path"] for item in workspace.list_files()}, {skill_path, input_path, reference})
        self.assertEqual(workspace.manifest["skills"][0]["digest"], self.selected_skill.digest)
        self.assertEqual({entry.name for entry in workspace.root.iterdir()}, {"skills", "inputs", "work", "out"})
        self.assertFalse((workspace.skills / "sample-skill" / ".env").exists())

        (self.skill / "SKILL.md").write_text("Edit the catalog copy.", encoding="utf-8")
        self.attachment.write_bytes(b"changed source")
        replay = self.prepare()
        self.assertEqual(replay.read_text(skill_path), "Read references.")
        self.assertEqual(replay.read_text(input_path), "an attached file")
        with self.assertRaisesRegex(ValueError, "固定输入"):
            self.store.create("session-a", "task-a", skills=[])

        (workspace.root / input_path).write_bytes(b"changed frozen input")
        with self.assertRaisesRegex(ValueError, "素材已变化"):
            self.prepare()

    def test_failed_materialization_leaves_no_reusable_task(self):
        self.attachment.write_bytes(b"changed source")
        with self.assertRaisesRegex(ValueError, "素材.*不一致"):
            self.prepare()
        self.assertEqual(list((self.store.root / "session-a").iterdir()), [])
        with self.assertRaises(FileNotFoundError):
            self.store.open("session-a", "task-a")

        (self.skill / "SKILL.md").write_text("Another Skill edition.", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Skill.*不一致"):
            self.prepare()

    def test_workspace_only_writes_scoped_text_paths(self):
        workspace = self.prepare()
        for path in ["out/../outside.txt", "/out/test.txt", r"out\name.txt", "out/name:stream",
                     "out//name.txt", "out/name.", "out/CoM¹.py", "out/NUL.txt", "out/LPT3",
                     "out/file\x00.txt", "work", "manifest.json", workspace.skill_files[0], workspace.input_files[0]]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                workspace.write_text(path, "denied")
        for session in ("../session-a", "CON", "Session-A", "session/a", "session:a"):
            with self.subTest(session=session), self.assertRaises(ValueError):
                self.store.open(session, "task-a")
        with self.assertRaises(ValueError):
            workspace.read_text("../manifest.json")
        with self.assertRaises(ValueError):
            workspace.write_text("work/big.txt", "x" * (512 * 1024 + 1))
        self.assertEqual(workspace.list_files()[-1]["path"], workspace.input_files[0])
        path = "work/scripts/compose.py"
        workspace.write_text(path, "print('草稿')\n")
        result = workspace.write_text(path, "print('成稿')\n")
        self.assertEqual(result, {"path": path, "size_bytes": len("print('成稿')\n".encode()),
                                  "sha256": hashlib.sha256("print('成稿')\n".encode()).hexdigest()})
        self.assertEqual(workspace.read_text(path), "print('成稿')\n")
        self.assertEqual(list((workspace.work / "scripts").iterdir()), [workspace.root / path])
        (workspace.work / "binary").write_bytes(b"\xff")
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            workspace.read_text("work/binary")

    def test_rejects_linked_workspace_and_skills_without_reading_target(self):
        workspace = self.prepare()
        external = self.root / "external"
        external.mkdir()
        (external / "private.txt").write_text("private file", encoding="utf-8")
        # Junctions exercise the Windows case that Path.is_symlink() misses.
        if os.name == "nt":
            from _winapi import CreateJunction

            def link(target, path):
                CreateJunction(str(target), str(path))
                self.addCleanup(path.rmdir)
        else:
            def link(target, path):
                path.symlink_to(target, target_is_directory=True)
                self.addCleanup(path.unlink)

        link(external, workspace.out / "linked")
        for action in (lambda: workspace.read_text("out/linked/private.txt"),
                       lambda: workspace.publish(["out/linked/private.txt"]),
                       lambda: workspace.write_text("out/linked/private.txt", "overwrite"),
                       workspace.list_files):
            with self.assertRaisesRegex(ValueError, "链接|联接"):
                action()
        self.assertEqual((external / "private.txt").read_text(encoding="utf-8"), "private file")

        link(external, self.skill / "linked")
        with self.assertRaisesRegex(ValueError, "链接|联接"):
            self.prepare(task="task-b")
        os.link(external / "private.txt", workspace.out / "hard.txt")
        with self.assertRaisesRegex(ValueError, "硬链接"):
            workspace.publish(["out/hard.txt"])

    def test_delivery_is_atomic_immutable_and_session_scoped(self):
        workspace = self.prepare()
        path = "out/results/合成图.txt"
        payload = "独立副本".encode()
        workspace.write_text(path, payload.decode())
        workspace.write_text("work/candidate.txt", "draft")
        with self.assertRaises(ValueError):
            workspace.publish(["work/candidate.txt"])
        with self.assertRaisesRegex(ValueError, "不能重复"):
            workspace.publish([path, path])
        with self.assertRaises(FileNotFoundError):
            workspace.publish([path, "out/missing.txt"])
        with self.assertRaises(FileNotFoundError):
            workspace.read_delivery(path)
        workspace.write_text("out/unselected.txt", "unsent")
        workspace.write_text("out/empty.txt", "")
        with self.assertRaisesRegex(ValueError, "不能为空"):
            workspace.publish([path, "out/empty.txt"])
        receipt, = workspace.publish([path])
        self.assertEqual(receipt, {"path": path, "name": "合成图.txt", "sha256": hashlib.sha256(payload).hexdigest(),
                                   "size_bytes": len(payload), "media_type": "text/plain"})

        workspace.write_text(path, "later edit")
        self.assertEqual(workspace.publish([path]), [receipt])
        reopened = SkillTaskWorkspaceStore(self.store.root).open("session-a", "task-a")
        self.assertEqual(reopened.read_delivery(path), (payload, receipt))
        with self.assertRaises(FileNotFoundError):
            reopened.read_delivery("out/unselected.txt")
        with self.assertRaises(FileNotFoundError):
            self.prepare(session="session-b").read_delivery(path)
        with self.assertRaisesRegex(ValueError, "已经提交"):
            workspace.publish(["out/unselected.txt"])

        (workspace.root.parent / "delivery" / "0").write_bytes(b"replaced")
        with self.assertRaisesRegex(ValueError, "回执不一致"):
            workspace.read_delivery(path)

    def test_concurrent_retries_agree_on_one_snapshot_and_receipt(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(self.prepare)
            second = executor.submit(self.prepare)
            first, second = first.result(timeout=10), second.result(timeout=10)
            self.assertEqual(first.manifest, second.manifest)
            first.write_text("out/result.txt", "finished")
            delivered = [executor.submit(workspace.publish, ["out/result.txt"]) for workspace in (first, second)]
            self.assertEqual(delivered[0].result(timeout=10), delivered[1].result(timeout=10))
