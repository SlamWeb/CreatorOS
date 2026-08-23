import os
import sys


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


def render_banner(word="CREATOROS"):
    lines = [""] * 5
    for character in word:
        glyph = _GLYPHS[character]
        for index, row in enumerate(glyph):
            lines[index] += row + "  "
    return lines


def print_banner():
    use_color = sys.stdout.isatty() and "NO_COLOR" not in os.environ
    print()
    for index, line in enumerate(render_banner()):
        prefix = _COLORS[index % len(_COLORS)] if use_color else ""
        suffix = _RESET if use_color else ""
        print(f"{prefix}{line}{suffix}")
    print("  CreatorOS Agent Runtime · learning build")
    print()
