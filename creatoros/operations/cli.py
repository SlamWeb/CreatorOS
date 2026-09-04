from __future__ import annotations

from creatoros.storage import PendingOperation, PendingOperationStatus
from creatoros.terminal import Console

from .models import OperationPlan, OperationPreview
from .service import PendingOperationError, PendingOperationService


CONFIRM_WORDS = {"确认", "确认执行", "执行", "confirm", "yes", "y"}
CANCEL_WORDS = {"取消", "cancel"}
BACK_WORDS = {"返回", "back", "b", "/menu", "q"}


class PendingOperationCLI:
    def __init__(self, console: Console, service: PendingOperationService):
        self.console = console
        self.service = service

    def run(self) -> None:
        actionable = self.service.list_actionable()
        active = actionable[0] if actionable else None
        if active is not None:
            self.console.write(f"\n◇ 已恢复待处理计划 {active.id[:8]}。")
            if len(actionable) > 1:
                self.console.write(f"  另有 {len(actionable) - 1} 个较早计划待处理。")
            self._render(active)
        else:
            self.console.write("\n输入栏目运营要求；输入“返回”回到主菜单。")

        while True:
            if active is None:
                request = self.console.prompt().strip()
                if request.lower() in BACK_WORDS:
                    return
                if not request:
                    continue
                try:
                    active = self.service.propose(request)
                except Exception as error:
                    self.console.write(f"⚠ 无法生成运营计划：{error}")
                    continue
                self._render(active)
                continue

            response = self.console.prompt().strip()
            normalized = response.lower()
            if normalized in BACK_WORDS:
                return
            if normalized in CANCEL_WORDS:
                try:
                    active = self.service.cancel(active.id, expected_version=active.version, expected_revision=active.revision)
                    self.console.write("◇ 已取消，不会修改栏目。")
                except PendingOperationError as error:
                    self.console.write(f"⚠ {error}")
                active = None
                continue
            if normalized in CONFIRM_WORDS:
                if active.status is not PendingOperationStatus.AWAITING_APPROVAL:
                    self.console.write("⚠ 当前计划还不能确认，请先补充或修改要求。")
                    continue
                try:
                    active = self.service.confirm(active.id, expected_version=active.version,
                                                  expected_revision=active.revision, confirmation_token=active.confirmation_token)
                except PendingOperationError as error:
                    self.console.write(f"⚠ {error}")
                    active = self.service.get(active.id)
                    self._render(active)
                    continue
                if active.status is PendingOperationStatus.SUCCEEDED:
                    self.console.write("✓ 计划已执行。")
                    active = None
                else:
                    self._render(active)
                    if active.status is PendingOperationStatus.FAILED:
                        active = None
                continue
            if not response:
                continue
            try:
                active = self.service.edit(active.id, response, expected_version=active.version, expected_revision=active.revision)
                self._render(active)
            except Exception as error:
                self.console.write(f"⚠ 无法修改计划：{error}")

    def _render(self, pending: PendingOperation) -> None:
        self.console.write("")
        self.console.write(
            f"计划 {pending.id[:8]}  ·  revision {pending.revision}  ·  {pending.status.value}"
        )
        if pending.message:
            self.console.write(f"  {pending.message}")
        if pending.plan_json and pending.preview_json:
            plan = OperationPlan.model_validate(pending.plan_json)
            preview = OperationPreview.model_validate(pending.preview_json)
            titles = {
                topic.topic_id: topic.title
                for operation in plan.operations
                if operation.action == "add_topics"
                for topic in operation.topics
            }
            for change in preview.changes:
                if change.action == "add_topics":
                    added = [item for item in change.after_order if item not in change.before_order]
                    for topic_id in added:
                        self.console.write(f"  + {titles.get(topic_id, topic_id)}")
                else:
                    self.console.write("  ↕ " + " → ".join(change.after_order))
            self.console.write("\n输入“确认”执行；直接输入修改要求；输入“取消”放弃。")
        elif pending.status is PendingOperationStatus.NEEDS_CLARIFICATION:
            self.console.write("直接补充缺少的信息，CreatorOS 会生成新 revision。")
        elif pending.status is PendingOperationStatus.STALE:
            self.console.write("数据库状态已变化，请输入修改要求以重新生成 Preview。")
        elif pending.status is PendingOperationStatus.FAILED and pending.error:
            self.console.write(f"⚠ 执行失败：{pending.error}")
