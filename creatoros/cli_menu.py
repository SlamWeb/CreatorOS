from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from rich.text import Text

from .terminal import Console, RichConsole


@dataclass(frozen=True)
class AuthorSummary:
    """Only the fields needed by the author directory screen."""

    author_id: str
    display_name: str
    status: str = "unknown"
    recommended_writer_prompt: str = "strong_identity"


class CreatorOSMenu:
    """Small menu-first shell around the existing Agent Runtime."""

    def __init__(
        self,
        console: Console,
        *,
        authors_loader: Callable[[], Sequence[AuthorSummary]],
        agent_runner: Callable[[], None] | None = None,
    ):
        self.console = console
        self.authors_loader = authors_loader
        self.agent_runner = agent_runner

    def run(self) -> None:
        screen = "home"
        authors: tuple[AuthorSummary, ...] = ()
        selected: AuthorSummary | None = None
        while True:
            if screen == "home":
                action = self._home()
                if action == "quit":
                    return
                if action == "chat":
                    if self.agent_runner is None:
                        self._notice("Agent 对话入口尚未配置。", warning=True)
                    else:
                        self.agent_runner()
                    continue
                if action == "authors":
                    try:
                        authors = tuple(self.authors_loader())
                    except Exception as error:
                        self._notice(f"作者目录暂不可用：{error}", warning=True)
                        continue
                    screen = "authors"
                elif action == "invalid":
                    self._notice("无效的菜单编号。", warning=True)
                else:
                    self._placeholder(action)
            elif screen == "authors":
                action = self._authors(authors)
                if action == "back":
                    screen = "home"
                elif action == "quit":
                    return
                elif isinstance(action, AuthorSummary):
                    selected = action
                    screen = "author"
            else:
                action = self._author_detail(selected)
                if action == "back":
                    screen = "authors"
                elif action == "quit":
                    return

    def _home(self) -> str:
        self._heading("CreatorOS  ·  运营控制台")
        self._write("当前：作者矩阵 / 热点路由 / Agent Runtime", "creatoros.secondary")
        self._write("")
        self._write("目录", "creatoros.logo.violet")
        self._write("  1  今日运营")
        self._write("  2  作者矩阵")
        self._write("  3  热点雷达")
        self._write("  4  运行记录")
        self._write("  5  Agent 对话")
        self._write("  q  退出", "creatoros.secondary")
        choice = self._choice("选择操作 › ")
        return {
            "1": "today",
            "2": "authors",
            "3": "radar",
            "4": "history",
            "5": "chat",
            "q": "quit",
        }.get(choice, "invalid")

    def _authors(
        self,
        authors: Sequence[AuthorSummary],
    ) -> AuthorSummary | str:
        self._heading("CreatorOS / 作者矩阵")
        if not authors:
            self._write("暂无可用作者。请先在 PersonClone 中添加作者。", "creatoros.warning")
        else:
            for index, author in enumerate(authors, start=1):
                self._write(
                    f"  {index}  {author.display_name}  "
                    f"{author.author_id}  ·  {author.status}"
                )
        self._write("")
        self._write("输入作者编号进入详情，b 返回，q 退出", "creatoros.secondary")
        choice = self._choice("选择作者 › ")
        if choice == "b":
            return "back"
        if choice == "q":
            return "quit"
        if choice.isdigit():
            index = int(choice) - 1
            if 0 <= index < len(authors):
                return authors[index]
        self._notice("无效的作者编号。", warning=True)
        return "stay"

    def _author_detail(self, author: AuthorSummary | None) -> str:
        if author is None:
            return "back"
        self._heading(f"CreatorOS / 作者矩阵 / {author.author_id}")
        self._write(f"作者：{author.display_name}")
        self._write(f"状态：{author.status}  ·  推荐模式：{author.recommended_writer_prompt}")
        self._write("")
        self._write("  1  热点队列")
        self._write("  2  常青队列")
        self._write("  3  实验队列")
        self._write("  4  查看作者画像")
        self._write("  b  返回    q  退出", "creatoros.secondary")
        choice = self._choice("选择入口 › ")
        if choice == "b":
            return "back"
        if choice == "q":
            return "quit"
        if choice in {"1", "2", "3", "4"}:
            self._placeholder(f"author_detail_{choice}")
        else:
            self._notice("无效的入口编号。", warning=True)
        return "stay"

    def _placeholder(self, action: str) -> None:
        labels = {
            "today": "今日运营",
            "radar": "热点雷达",
            "history": "运行记录",
            "author_detail_1": "热点队列",
            "author_detail_2": "常青队列",
            "author_detail_3": "实验队列",
            "author_detail_4": "作者画像",
        }
        self._notice(f"{labels.get(action, action)}将在下一步接入。")

    def _heading(self, title: str) -> None:
        self._write("")
        self._write(title, "creatoros.logo.violet")
        self._write("─" * max(28, len(title) + 4), "creatoros.secondary")

    def _notice(self, message: str, *, warning: bool = False) -> None:
        self._write(("⚠ " if warning else "◇ ") + message, "creatoros.warning" if warning else "creatoros.secondary")

    def _choice(self, prompt: str) -> str:
        try:
            return self.console.prompt(prompt).strip().lower()
        except EOFError:
            return "q"

    def _write(self, message: str, style: str | None = None) -> None:
        if isinstance(self.console, RichConsole) and style:
            self.console.rich.print(Text(message, style=style), soft_wrap=True)
        else:
            self.console.write(message)
