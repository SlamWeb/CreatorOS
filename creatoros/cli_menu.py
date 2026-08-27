from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from rich.text import Text

from .menu_input import MenuSelect
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
        self.selector = MenuSelect(console)

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
        choice = self.selector.choose(
            "目录",
            ("今日运营", "作者矩阵", "热点雷达", "运行记录", "Agent 对话"),
            escape_result="q",
        )
        if choice == "q":
            return "quit"
        if isinstance(choice, int):
            return ("today", "authors", "radar", "history", "chat")[choice]
        return "invalid"

    def _authors(
        self,
        authors: Sequence[AuthorSummary],
    ) -> AuthorSummary | str:
        self._heading("CreatorOS / 作者矩阵")
        if not authors:
            self._write("暂无可用作者。请先在 PersonClone 中添加作者。", "creatoros.warning")
        self._write("")
        labels = tuple(
            f"{author.display_name}  {author.author_id}  ·  {author.status}"
            for author in authors
        )
        choice = self.selector.choose("选择作者", labels, escape_result="back")
        if choice == "back":
            return "back"
        if choice == "q":
            return "quit"
        if isinstance(choice, int):
            return authors[choice]
        self._notice("无效的作者选择。", warning=True)
        return "stay"

    def _author_detail(self, author: AuthorSummary | None) -> str:
        if author is None:
            return "back"
        self._heading(f"CreatorOS / 作者矩阵 / {author.author_id}")
        self._write(f"作者：{author.display_name}")
        self._write(f"状态：{author.status}  ·  推荐模式：{author.recommended_writer_prompt}")
        self._write("")
        choice = self.selector.choose(
            "选择入口",
            ("热点队列", "常青队列", "实验队列", "查看作者画像"),
            escape_result="back",
        )
        if choice == "back":
            return "back"
        if choice == "q":
            return "quit"
        if isinstance(choice, int):
            self._placeholder(f"author_detail_{choice + 1}")
        else:
            self._notice("无效的入口选择。", warning=True)
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

    def _write(self, message: str, style: str | None = None) -> None:
        if isinstance(self.console, RichConsole) and style:
            self.console.rich.print(Text(message, style=style), soft_wrap=True)
        else:
            self.console.write(message)
