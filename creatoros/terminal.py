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
_SPINNER_FRAMES = ("|", "/", "-", "\\")


class Console:
    """Small terminal I/O adapter used by the runtime."""

    def __init__(self, input_fn=input, output=None):
        self.input_fn = input_fn
        self.output = output if output is not None else sys.stdout
        self.spinner_index = 0

    def prompt(self, text=""):
        return self.input_fn(text)

    def write(self, text="", end="\n", flush=False):
        print(text, end=end, file=self.output, flush=flush)

    def banner(self):
        print_banner(output=self.output)

    def render_event(self, event: AgentEvent):
        if event.kind == "turn_start":
            frame = _SPINNER_FRAMES[self.spinner_index]
            self.spinner_index = (self.spinner_index + 1) % len(_SPINNER_FRAMES)
            self.write(f"\n{frame} Agent 思考中...\nAgent: ", end="", flush=True)
        elif event.kind == "session_reset":
            self.write("[Session] 已清空当前会话。")
        elif event.kind == "guard_stop":
            self.write(f"! [Guard] 本次任务已达到最大模型调用次数：{event.data['max_turns']}")
        elif event.kind == "tool_call":
            self.write(f"* [Tool call] 正在调用：{event.data['name']}")
        elif event.kind == "tool_result":
            self.write(f"+ [Tool result] 已完成：{event.data['content']}")
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
