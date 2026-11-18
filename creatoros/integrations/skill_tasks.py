"""Turn-scoped Skill execution and delivery, independent of the model provider."""
from dataclasses import asdict
from urllib.parse import urlencode

from .skill_authority import prepare_skill_authority, verify_skill_authority
from .producer_skills import _SKILL_EDIT_LOCK
from .skill_task_workspace import SkillTaskSkill


class SkillTaskError(ValueError):
    pass


class SkillTaskTurn:
    def __init__(self, store, runner, library, *, session_id, creator_id, turn_id, inputs, check_account):
        self.store = store
        self.runner = runner
        self.library = library
        self.session_id = session_id
        self.creator_id = creator_id
        self.turn_id = turn_id
        self.inputs = inputs
        self.check_account = check_account
        self.workspace = None
        self.selected = None

    def prepare(self, skill_ids):
        selected = tuple(skill_ids or ())
        if len(set(selected)) != len(selected):
            raise SkillTaskError("请选择不同的 Skill。")
        if self.workspace is not None:
            if selected != self.selected:
                raise SkillTaskError("本轮任务已冻结 Skill 和输入，请在下一条消息重新选择。")
            return self.workspace.manifest
        catalog = self.library.catalog
        with _SKILL_EDIT_LOCK:
            proof = prepare_skill_authority(self.library.db, self.library, self.creator_id, selected)
            if not proof.allowed:
                raise SkillTaskError("指定 Skill 的版本不可用或不属于当前账号。")
            frozen = []
            for skill_id in selected:
                directory, digest = catalog.snapshot_source(skill_id)
                frozen.append(SkillTaskSkill(skill_id, directory, digest))
            with self.library.db.session() as session:
                verify_skill_authority(session, proof)
            workspace = self.store.create(self.session_id, self.turn_id, skills=frozen, inputs=self.inputs())
        self.workspace, self.selected = workspace, selected
        return workspace.manifest

    def perform(self, operation, *, skills, path, content, args, paths):
        self.check_account()
        if operation == "prepare":
            manifest = self.prepare(skills)
            return {"task_id": self.turn_id, **manifest, "files": self.workspace.list_files(),
                    "script_cwd": "workspace 根目录。输入在 inputs/，可写 work/ 和 out/。"}
        workspace = self.workspace
        if workspace is None:
            raise SkillTaskError("请先 prepare 本轮的任务文件。")
        if operation == "list":
            return {"files": workspace.list_files()}
        if operation == "read":
            return {"path": path, "content": workspace.read_text(path)}
        if operation == "write":
            return workspace.write_text(path, content)
        if operation == "run":
            # A nonzero script exit is an observed outcome, so the model can
            # use stderr to correct its work inside the same task.
            workspace.verify_inputs()
            return asdict(self.runner.run(workspace.root, path, args or ()))
        if operation == "publish":
            base = f"/api/agent/sessions/{self.session_id}/skill-tasks/{self.turn_id}/download"
            return {"status": "published", "session_id": self.session_id, "task_id": self.turn_id,
                    "deliverables": [{**item, "url": base + "?" + urlencode({"path": item["path"]})}
                                     for item in workspace.publish(paths)]}
        raise SkillTaskError("未知的文件操作。")
