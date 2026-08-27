from io import StringIO

from creatoros.cli_menu import AuthorSummary, CreatorOSMenu
from creatoros.terminal import RichConsole


def main():
    output = StringIO()
    inputs = iter(("2", "1", "1", "b", "b", "5", "/exit", "q"))
    agent_calls = []

    def fake_input(prompt):
        return next(inputs)

    authors = (
        AuthorSummary("alice", "Alice", "ready", "strong_identity"),
        AuthorSummary("bob", "Bob", "empty", "strong_identity"),
    )
    menu = CreatorOSMenu(
        RichConsole(input_fn=fake_input, output=output),
        authors_loader=lambda: authors,
        agent_runner=lambda: agent_calls.append("called"),
    )
    menu.run()

    rendered = output.getvalue()
    assert agent_calls == ["called"]
    assert "CreatorOS" in rendered
    assert "运营工作台" not in rendered
    assert "工作区" not in rendered
    assert "热点发现  ·  作者路由  ·  内容生产  ·  发布" not in rendered
    assert "CreatorOS / 作者矩阵" in rendered
    assert "CreatorOS / 作者矩阵 / alice" in rendered
    assert "已接入 2 位作者" in rendered
    assert "热点队列将在下一步接入" in rendered
    assert "常青队列" in rendered
    assert "实验队列" in rendered
    assert "无效的作者编号" not in rendered
    assert "┌" not in rendered and "╭" not in rendered
    print("cli_menu_smoke=passed")


if __name__ == "__main__":
    main()
