import os
import sys

from rich.align import Align
from rich.console import Console as RichTerminalConsole
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from .events import AgentEvent

_GLYPHS = {
    "C": [" ████ ", "██    ", "██    ", "██    ", " ████ "],
    "R": ["█████ ", "██  ██", "█████ ", "██ ██ ", "██  ██"],
    "E": ["██████", "██    ", "████  ", "██    ", "██████"],
    "A": [" ████ ", "██  ██", "██████", "██  ██", "██  ██"],
    "T": ["██████", "  ██  ", "  ██  ", "  ██  ", "  ██  "],
    "O": [" ████ ", "██  ██", "██  ██", "██  ██", " ████ "],
    "S": [" █████", "██    ", " ████ ", "    ██", "█████ "],
}
_COLORS = ["\033[96m", "\033[94m", "\033[95m", "\033[93m", "\033[92m"]
_RESET = "\033[0m"
_PROMPT = "❯ "
_SPINNER_FRAMES = ("◌", "◍", "◎", "●")
_CYAN = "\033[96m"
_BLUE = "\033[94m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"


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
            status = self._style(f"{frame} 思考中", _BLUE)
            self.write(f"\n  {status}\n  ", end="", flush=True)
        elif event.kind == "session_reset":
            self.write("[Session] 已清空当前会话。")
        elif event.kind == "guard_stop":
            text = f"⚠ [Guard] 本次任务已达到最大模型调用次数：{event.data['max_turns']}"
            self.write(self._style(text, _YELLOW))
        elif event.kind == "tool_call":
            text = f"  ↳ [Tool call] 正在调用 · {event.data['name']}"
            self.write(self._style(text, _CYAN))
        elif event.kind == "tool_result":
            text = f"  ✓ [Tool result] 已完成 · {event.data['content']}"
            self.write(self._style(text, _GREEN))
        elif event.kind == "session_saved":
            self.write("\n[Session] 已保存当前会话。")


class RichConsole(Console):
    """Rich-backed renderer that keeps the runtime's Console interface."""

    def __init__(self, input_fn=input, output=None):
        super().__init__(input_fn=input_fn, output=output)
        self.rich = RichTerminalConsole(
            file=self.output,
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
            prompt = Text(text, style="bright_cyan")
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
        text = Text()
        line_styles = ("bright_cyan", "bright_blue", "magenta", "yellow", "green")
        for index, line in enumerate(render_banner()):
            text.append(line + "\n", style=line_styles[index])
        text.rstrip()
        panel = Panel(
            Align.center(text),
            title=Text("CreatorOS", style="bold bright_white"),
            subtitle=Text("Agent Runtime · learning build", style="dim cyan"),
            border_style="bright_blue",
            padding=(1, 2),
            expand=False,
        )
        self.rich.print(panel)

    def render_event(self, event: AgentEvent):
        if event.kind == "turn_start":
            self._stop_active()
            self._status = self.rich.status(
                "思考中",
                spinner="dots",
                spinner_style="bright_cyan",
            )
            self._status.start()
        elif event.kind == "session_reset":
            self._stop_active()
            self.rich.print("[Session] 已清空当前会话。", style="yellow")
        elif event.kind == "guard_stop":
            self._stop_active()
            self.rich.print(
                f"⚠ [Guard] 本次任务已达到最大模型调用次数：{event.data['max_turns']}",
                style="bold yellow",
            )
        elif event.kind == "tool_call":
            self._stop_active()
            self.rich.print(
                f"  ↳ [Tool call] 正在调用 · {event.data['name']}",
                style="bright_cyan",
            )
        elif event.kind == "tool_result":
            self._stop_active()
            self.rich.print(
                f"  ✓ [Tool result] 已完成 · {event.data['content']}",
                style="green",
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
        return Panel(
            Markdown(self._stream_buffer or " "),
            border_style="bright_blue",
            padding=(0, 1),
            expand=False,
        )

    def _stop_status(self):
        if self._status is not None:
            self._status.stop()
            self._status = None

    def _stop_active(self):
        self._stop_stream()
        self._stop_status()


def render_banner(word="CREATOROS"):
    lines = [""] * 5
    for character in word:
        glyph = _GLYPHS[character]
        for index, row in enumerate(glyph):
            lines[index] += row + "  "
    return lines


def print_banner(output=None):
    output = output if output is not None else sys.stdout
    is_tty = getattr(output, "isatty", lambda: False)()
    use_color = is_tty and "NO_COLOR" not in os.environ
    print(file=output)
    for index, line in enumerate(render_banner()):
        prefix = _COLORS[index % len(_COLORS)] if use_color else ""
        suffix = _RESET if use_color else ""
        print(f"{prefix}{line}{suffix}", file=output)
    print("  CreatorOS Agent Runtime · learning build", file=output)
    print(file=output)
