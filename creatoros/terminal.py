import os
import sys

from rich.console import Console as RichTerminalConsole
from rich.live import Live
from rich.markdown import Markdown
from rich.theme import Theme
from rich.text import Text

from .events import AgentEvent

_WORDMARK = "CreatorOS"
_BRAND = "\033[35m"
_RESET = "\033[0m"
_PROMPT = "❯ "
_SPINNER_FRAMES = ("◌", "◍", "◎", "●")
_CYAN = "\033[36m"
_SLATE = "\033[90m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"

_RICH_THEME = Theme(
    {
        "creatoros.brand": "bold #c4b5fd",
        "creatoros.prompt": "#7dd3fc",
        "creatoros.thinking": "dim #94a3b8",
        "creatoros.tool": "dim #94a3b8",
        "creatoros.success": "#86efac",
        "creatoros.warning": "#fcd34d",
        "creatoros.secondary": "dim",
    }
)


class Console:
    """Small terminal I/O adapter used by the runtime."""

    def __init__(self, input_fn=input, output=None):
        self.input_fn = input_fn
        self.output = output if output is not None else sys.stdout
        self.spinner_index = 0
        self.use_color = (
            getattr(self.output, "isatty", lambda: False)()
            and "NO_COLOR" not in os.environ
        )

    def prompt(self, text=_PROMPT):
        return self.input_fn(self._style(text, _CYAN))

    def write(self, text="", end="\n", flush=False):
        print(text, end=end, file=self.output, flush=flush)

    def banner(self):
        print_banner(output=self.output)

    def _style(self, text, color):
        if not self.use_color:
            return text
        return f"{color}{text}{_RESET}"

    def render_event(self, event: AgentEvent):
        if event.kind == "turn_start":
            frame = _SPINNER_FRAMES[self.spinner_index]
            self.spinner_index = (self.spinner_index + 1) % len(_SPINNER_FRAMES)
            status = self._style(f"{frame} 思考中", _SLATE)
            self.write(f"\n  {status}\n  ", end="", flush=True)
        elif event.kind == "session_reset":
            self.write("[Session] 已清空当前会话。")
        elif event.kind == "guard_stop":
            text = f"⚠ [Guard] 本次任务已达到最大模型调用次数：{event.data['max_turns']}"
            self.write(self._style(text, _YELLOW))
        elif event.kind == "tool_call":
            text = f"  ↳ {event.data['name']}"
            self.write(self._style(text, _SLATE))
        elif event.kind == "tool_result":
            text = f"  ✓ done · {event.data['content']}"
            self.write(self._style(text, _GREEN))
        elif event.kind == "session_saved":
            self.write("\n[Session] 已保存当前会话。")


class RichConsole(Console):
    """Rich-backed renderer that keeps the runtime's Console interface."""

    def __init__(self, input_fn=input, output=None):
        super().__init__(input_fn=input_fn, output=output)
        self.rich = RichTerminalConsole(
            file=self.output,
            theme=_RICH_THEME,
            markup=False,
            emoji=False,
            soft_wrap=True,
            no_color=not self.use_color,
        )
        self._status = None
        self._live = None
        self._stream_buffer = ""

    def prompt(self, text=_PROMPT):
        if self.input_fn is input:
            prompt = Text(text, style="creatoros.prompt")
            return self.rich.input(prompt, markup=False, emoji=False)
        return super().prompt(text)

    def write(self, text="", end="\n", flush=False):
        if self._status is not None and text:
            self._stop_status()
            self._start_stream()

        if self._live is not None:
            if text:
                self._stream_buffer += text
                self._live.update(self._stream_renderable())
            if end:
                self._stop_stream()
            return

        self.rich.print(text, end=end, markup=False, emoji=False, soft_wrap=True)
        if flush:
            self.output.flush()

    def banner(self):
        self.rich.print(Text(_WORDMARK, style="creatoros.brand"), soft_wrap=False)
        self.rich.print()

    def render_event(self, event: AgentEvent):
        if event.kind == "turn_start":
            self._stop_active()
            self._status = self.rich.status(
                "思考中",
                spinner="dots",
                spinner_style="creatoros.thinking",
            )
            self._status.start()
        elif event.kind == "session_reset":
            self._stop_active()
            self.rich.print("[Session] 已清空当前会话。", style="creatoros.warning")
        elif event.kind == "guard_stop":
            self._stop_active()
            self.rich.print(
                f"⚠ [Guard] 本次任务已达到最大模型调用次数：{event.data['max_turns']}",
                style="creatoros.warning",
            )
        elif event.kind == "tool_call":
            self._stop_active()
            self.rich.print(
                f"  ↳ {event.data['name']}",
                style="creatoros.tool",
            )
        elif event.kind == "tool_result":
            self._stop_active()
            self.rich.print(
                f"  ✓ done · {event.data['content']}",
                style="creatoros.success",
                soft_wrap=True,
            )
        elif event.kind == "session_saved":
            self._stop_active()
            self.rich.print("\n[Session] 已保存当前会话。", style="dim")

    def _start_stream(self):
        self._stream_buffer = ""
        self._live = Live(
            self._stream_renderable(),
            console=self.rich,
            refresh_per_second=12,
            transient=False,
        )
        self._live.start()

    def _stop_stream(self):
        if self._live is not None:
            self._live.stop()
            self._live = None
            self.rich.print()

    def _stream_renderable(self):
        return Markdown(self._stream_buffer or " ")

    def _stop_status(self):
        if self._status is not None:
            self._status.stop()
            self._status = None

    def _stop_active(self):
        self._stop_stream()
        self._stop_status()


def render_banner(word=_WORDMARK):
    return [word]


def print_banner(output=None):
    output = output if output is not None else sys.stdout
    is_tty = getattr(output, "isatty", lambda: False)()
    use_color = is_tty and "NO_COLOR" not in os.environ
    print(file=output)
    for line in render_banner():
        prefix = _BRAND if use_color else ""
        suffix = _RESET if use_color else ""
        print(f"{prefix}{line}{suffix}", file=output)
    print(file=output)
