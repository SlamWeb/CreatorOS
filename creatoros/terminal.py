import os
import sys

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
