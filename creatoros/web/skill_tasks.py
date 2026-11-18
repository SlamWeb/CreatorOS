"""Bind browser authorization and account resources to the Skill task interface."""
from pathlib import Path

from fastapi import HTTPException

from ..integrations.skill_task_workspace import SkillTaskInput, SkillTaskWorkspaceStore
from ..integrations.skill_tasks import SkillTaskError, SkillTaskTurn


class ChatSkillTasks:
    def __init__(self, chat, library, materials):
        self.chat = chat
        self.library = library
        self.materials = materials
        self.store = SkillTaskWorkspaceStore(chat.root / "_skill-tasks")

    def for_turn(self, session_id, creator_id, turn_id, attachments, materials):
        from ..integrations.skill_task_runner import SkillTaskRunner
        def check_account():
            try:
                self.chat.require_creator(creator_id)
            except HTTPException as error:
                raise SkillTaskError(error.detail) from error
            if self.chat.stopping.is_set():
                raise SkillTaskError("服务正在关闭。")

        def freeze_inputs():
            result = []
            suffixes = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
            for index, item in enumerate(attachments or (), 1):
                try:
                    source = self.chat.attachments.path(session_id, item["id"])
                except HTTPException as error:
                    raise SkillTaskError(error.detail) from error
                # Use numbered safe paths and retain the input order of the message.
                name = f"image-{index:02d}" + suffixes[item["mime_type"]]
                result.append(SkillTaskInput(name, source, item["id"]))
            for index, item in enumerate(materials or (), 1):
                current = self.materials.resolve(creator_id, item["id"])
                if current["sha256"] != item["sha256"]:
                    raise SkillTaskError("附件音频已变化，请重新选取。")
                source = Path(current["path"])
                result.append(SkillTaskInput(f"material-{index:02d}" + source.suffix, source, item["sha256"]))
            return result

        return SkillTaskTurn(self.store, SkillTaskRunner(), self.library, session_id=session_id, creator_id=creator_id,
                             turn_id=turn_id, inputs=freeze_inputs, check_account=check_account)

    def download(self, session_id, turn_id, path):
        with self.chat.lock:
            doc = self.chat._read(session_id)
            if not doc.get("creator_id") or not any(
                    req["id"] == turn_id and req.get("skill_workspace") for req in doc["requests"]):
                raise HTTPException(404, "交付文件不存在。")
        try:
            return self.store.open(session_id, turn_id).read_delivery(path)
        except (OSError, ValueError, KeyError) as error:
            raise HTTPException(404, "交付文件不存在或已变化。") from error
