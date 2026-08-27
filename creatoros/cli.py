import json
import os

from .agent.loop import run_agent
from .ai.deepseek import DeepSeekProvider
from .ai.provider import ModelProvider
from .cli_menu import AuthorSummary, CreatorOSMenu
from .terminal import RichConsole
from .tools import list_authors


def main():
    console = RichConsole()
    console.banner()

    def run_agent_mode():
        provider: ModelProvider = DeepSeekProvider(
            api_key=os.environ["DEEPSEEK_API_KEY"]
        )
        run_agent(provider, console=console)

    CreatorOSMenu(
        console,
        authors_loader=load_author_summaries,
        agent_runner=run_agent_mode,
    ).run()


def load_author_summaries() -> tuple[AuthorSummary, ...]:
    """Load only directory-safe author fields through the existing Tool boundary."""
    result = list_authors()
    if result.is_error:
        raise RuntimeError(result.content)
    try:
        payload = json.loads(result.content)
    except json.JSONDecodeError as error:
        raise RuntimeError("作者目录返回了无效 JSON。") from error

    authors = payload.get("authors", [])
    if not isinstance(authors, list):
        return ()
    summaries: list[AuthorSummary] = []
    for item in authors:
        if not isinstance(item, dict) or not isinstance(item.get("author"), str):
            continue
        content_count = item.get("content_count") or 0
        status = "ready" if isinstance(content_count, int) and content_count > 0 else "empty"
        summaries.append(
            AuthorSummary(
                author_id=item["author"],
                display_name=str(item.get("display_name") or item["author"]),
                status=status,
                recommended_writer_prompt=str(
                    item.get("recommended_writer_prompt") or "strong_identity"
                ),
            )
        )
    return tuple(summaries)
