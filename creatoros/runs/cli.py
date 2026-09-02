from __future__ import annotations

from creatoros.menu_input import MenuSelect
from creatoros.storage import ContentRepository, ContentRunStatus, Topic, TopicStatus
from creatoros.terminal import Console

from .service import ContentRunError, ContentRunExecutionError, ContentRunService


STATUS_LABELS = {
    ContentRunStatus.QUEUED: "待生产",
    ContentRunStatus.PRODUCING: "生产中",
    ContentRunStatus.VALIDATING: "验收中",
    ContentRunStatus.AWAITING_APPROVAL: "待批准",
    ContentRunStatus.APPROVED: "已批准",
    ContentRunStatus.INTERRUPTED: "已中断",
    ContentRunStatus.FAILED: "失败",
    ContentRunStatus.CANCELLED: "已取消",
}


class ContentRunCLI:
    def __init__(
        self,
        console: Console,
        service: ContentRunService,
        content_repository: ContentRepository,
    ):
        self.console = console
        self.service = service
        self.content_repository = content_repository
        self.selector = MenuSelect(console)

    def run(self) -> None:
        interrupted, validated, failed = self.service.recover_inflight()
        if interrupted or validated or failed:
            self.console.write(
                f"\n◇ 启动恢复：中断 {interrupted} · 完成验收 {validated} · 验收失败 {failed}"
            )
        while True:
            runs = self.service.list_runs()
            labels = tuple(self._run_label(item) for item in runs) + (
                "＋ 从选题队列开始生产",
                "返回主菜单",
            )
            choice = self.selector.choose("", labels, escape_result="back")
            if choice in {"back", "q"}:
                return
            if not isinstance(choice, int):
                self.console.write("⚠ 无效选择。")
                continue
            if choice < len(runs):
                self._run_detail(runs[choice].id)
            elif choice == len(runs):
                self._create_from_topic()
            else:
                return

    def _create_from_topic(self) -> None:
        topics = self._available_topics()
        if not topics:
            self.console.write("◇ 暂无待生产选题，请先在今日运营中加入 Topic。")
            return
        labels = tuple(f"{topic.title}  ·  {topic.series_id}" for topic in topics)
        choice = self.selector.choose("选择选题", labels, escape_result="back")
        if not isinstance(choice, int):
            return
        content_run = self.service.create(topics[choice].id)
        self.console.write(f"◇ 已创建 ContentRun {content_run.id[:8]}。")
        self._run_detail(content_run.id)

    def _run_detail(self, run_id: str) -> None:
        while True:
            content_run = self.service.get(run_id)
            revision = self.service.get_active_revision(run_id)
            self._render(content_run, revision)
            actions = self._actions(content_run.status, content_run.retryable)
            choice = self.selector.choose("", tuple(label for _, label in actions), escape_result="back")
            if choice in {"back", "q"} or not isinstance(choice, int):
                return
            action = actions[choice][0]
            if action == "back":
                return
            if action == "execute":
                self._execute(run_id)
            elif action == "approve":
                if not revision.artifact_digest:
                    self.console.write("⚠ 当前 Revision 缺少 digest。")
                    continue
                try:
                    self.service.approve(
                        run_id,
                        revision_id=revision.id,
                        artifact_digest=revision.artifact_digest,
                        expected_version=content_run.version,
                    )
                    self.console.write("✓ 已批准当前产物。")
                except ContentRunError as error:
                    self.console.write(f"⚠ {error}")
            elif action == "revise":
                instruction = self.console.prompt("返工要求 › ").strip()
                if not instruction:
                    continue
                try:
                    self.service.request_revision(
                        run_id,
                        instruction,
                        expected_version=content_run.version,
                    )
                    self.console.write("◇ 已创建新 Revision，旧产物保留。")
                except ContentRunError as error:
                    self.console.write(f"⚠ {error}")
            elif action == "cancel":
                try:
                    self.service.cancel(run_id, expected_version=content_run.version)
                    self.console.write("◇ 已取消运行，已有产物仍保留。")
                except ContentRunError as error:
                    self.console.write(f"⚠ {error}")

    def _execute(self, run_id: str) -> None:
        try:
            with self.console.activity("Codex 正在生产内容 · Ctrl+C 中断"):
                result = self.service.execute(run_id)
            self.console.write(f"✓ 产物已通过确定性验收 · {result.artifact_digest[:12]}")
        except KeyboardInterrupt:
            self.console.write("\n◇ 已中断并保存状态，可稍后显式恢复。")
        except ContentRunExecutionError as error:
            self.console.write(f"⚠ 生产失败，状态已保存：{error}")
        except ContentRunError as error:
            self.console.write(f"⚠ {error}")

    def _available_topics(self) -> tuple[Topic, ...]:
        topics = (
            topic
            for series in self.content_repository.list_series()
            for topic in self.content_repository.list_topics(series.id)
        )
        return tuple(topic for topic in topics if topic.status is TopicStatus.QUEUED)

    def _render(self, content_run, revision) -> None:
        snapshot = content_run.input_snapshot_json
        self.console.write("")
        self.console.write(
            f"{snapshot['topic_title']}  ·  {STATUS_LABELS[content_run.status]}"
        )
        self.console.write(
            f"Run {content_run.id[:8]}  ·  Revision {revision.revision_number}  ·  version {content_run.version}"
        )
        if revision.artifact_directory:
            self.console.write(f"产物：{revision.artifact_directory}")
        if revision.artifact_digest:
            self.console.write(f"Digest：{revision.artifact_digest[:16]}…")
        if content_run.error_message:
            self.console.write(f"⚠ {content_run.failure_stage} · {content_run.error_message}")

    @staticmethod
    def _run_label(content_run) -> str:
        title = content_run.input_snapshot_json.get("topic_title", content_run.topic_id)
        return f"{title}  ·  {STATUS_LABELS[content_run.status]}  ·  r{content_run.active_revision_number}"

    @staticmethod
    def _actions(status: ContentRunStatus, retryable: bool) -> list[tuple[str, str]]:
        if status is ContentRunStatus.QUEUED:
            return [("execute", "开始生产"), ("cancel", "取消"), ("back", "返回")]
        if status is ContentRunStatus.INTERRUPTED or (
            status is ContentRunStatus.FAILED and retryable
        ):
            return [("execute", "恢复生产"), ("revise", "新建返工版本"), ("cancel", "取消"), ("back", "返回")]
        if status is ContentRunStatus.AWAITING_APPROVAL:
            return [("approve", "批准产物"), ("revise", "提出返工"), ("cancel", "取消"), ("back", "返回")]
        if status is ContentRunStatus.FAILED:
            return [("revise", "新建返工版本"), ("cancel", "取消"), ("back", "返回")]
        return [("back", "返回")]
