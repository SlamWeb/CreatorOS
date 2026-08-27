from dataclasses import dataclass


@dataclass(frozen=True)
class SlashCommand:
    name: str
    description: str


AGENT_SLASH_COMMANDS = (
    SlashCommand("/help", "查看命令说明"),
    SlashCommand("/menu", "返回 CreatorOS 菜单"),
    SlashCommand("/context", "查看当前上下文使用量"),
    SlashCommand("/reset", "清空当前会话"),
    SlashCommand("/exit", "返回 CreatorOS 菜单"),
)


def render_command_help() -> str:
    return "\n".join(
        f"{command.name:<9} {command.description}"
        for command in AGENT_SLASH_COMMANDS
    )
