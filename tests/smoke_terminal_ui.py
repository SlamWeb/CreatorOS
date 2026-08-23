from contextlib import redirect_stdout
from io import StringIO

from creatoros.terminal import print_banner, render_banner


def main():
    lines = render_banner()
    assert len(lines) == 7
    assert all(line.strip() for line in lines)

    output = StringIO()
    with redirect_stdout(output):
        print_banner()
    assert "████" in output.getvalue()
    assert "learning build" not in output.getvalue()
    print("terminal_ui_smoke=passed")


if __name__ == "__main__":
    main()
