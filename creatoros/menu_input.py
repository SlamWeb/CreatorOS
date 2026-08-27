from __future__ import annotations

import sys
from collections.abc import Sequence

from .terminal import Console


class MenuSelect:
    """Cross-platform arrow-key selector with a line-input fallback."""

    def __init__(self, console: Console):
        self.console = console

    def choose(
        self,
        title: str,
        options: Sequence[str],
        *,
        escape_result: str,
    ) -> int | str:
        if self._can_use_keys():
            return self._interactive(title, options, escape_result)
        return self._line(title, options, escape_result)

    def _can_use_keys(self) -> bool:
        return (
            self.console.input_fn is input
            and getattr(self.console.output, "isatty", lambda: False)()
            and getattr(sys.stdin, "isatty", lambda: False)()
        )

    def _line(
        self,
        title: str,
        options: Sequence[str],
        escape_result: str,
    ) -> int | str:
        for index, option in enumerate(options, start=1):
            self.console.write(f"  {index}  {option}")
        self.console.write("↑↓ 移动 · Enter 进入 · Esc 返回 · q 退出", end="")
        try:
            raw = self.console.prompt(f"\n{title} › ").strip().lower()
        except EOFError:
            return "q"
        if raw == "q":
            return "q"
        if raw == "b" and escape_result == "back":
            return "back"
        if raw.isdigit():
            index = int(raw) - 1
            if 0 <= index < len(options):
                return index
        return "invalid"

    def _interactive(
        self,
        title: str,
        options: Sequence[str],
        escape_result: str,
    ) -> int | str:
        from prompt_toolkit.application import Application
        from prompt_toolkit.formatted_text import FormattedText
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import Layout, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.styles import Style

        selected = 0

        def render() -> FormattedText:
            fragments: list[tuple[str, str]] = [
                ("class:title", f"{title}\n"),
                ("class:hint", "↑↓ 移动 · Enter 进入 · Esc 返回 · q 退出\n\n"),
            ]
            for index, option in enumerate(options):
                marker = "❯" if index == selected else " "
                style = "class:selected" if index == selected else "class:option"
                fragments.append(("class:number", f"  {index + 1:>2}  "))
                fragments.append((style, f"{marker} {option}\n"))
            return FormattedText(fragments)

        bindings = KeyBindings()

        @bindings.add("up")
        def move_up(event):
            nonlocal selected
            if options:
                selected = (selected - 1) % len(options)
                event.app.invalidate()

        @bindings.add("down")
        def move_down(event):
            nonlocal selected
            if options:
                selected = (selected + 1) % len(options)
                event.app.invalidate()

        @bindings.add("enter")
        def accept(event):
            event.app.exit(result=selected if options else "invalid")

        @bindings.add("escape")
        def escape(event):
            event.app.exit(result=escape_result)

        @bindings.add("q")
        def quit_menu(event):
            event.app.exit(result="q")

        if escape_result == "back":

            @bindings.add("b")
            def back(event):
                event.app.exit(result="back")

        application = Application(
            layout=Layout(Window(FormattedTextControl(render))),
            key_bindings=bindings,
            full_screen=False,
            mouse_support=False,
            erase_when_done=True,
            style=Style.from_dict(
                {
                    "title": "bold #d8b4fe",
                    "hint": "#64748b",
                    "selected": "bold #ddd6fe",
                    "option": "#cbd5e1",
                    "number": "#64748b",
                }
            ),
        )
        try:
            return application.run()
        except KeyboardInterrupt:
            return "q"
