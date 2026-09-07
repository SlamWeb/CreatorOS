import json
import os
import argparse

from .agent.loop import run_agent
from .ai.deepseek import DeepSeekProvider
from .ai.provider import ModelProvider
from .cli_menu import AuthorSummary, CreatorOSMenu
from .config import DATABASE_URL
from .operations import OperationPlanParser, PendingOperationService
from .operations.cli import PendingOperationCLI
from .runs import ContentRunCLI, ContentRunService
from .runs.ownership import ExecutionOwnershipError
from .storage import ContentRepository, Database, upgrade_database
from .terminal import RichConsole
from .tools import list_authors


def main(argv=None):
    parser = argparse.ArgumentParser(description="CreatorOS CLI")
    parser.add_argument("--agent", action="store_true", help="连接 Studio 的 Agent 对话；不获取生产锁")
    parser.add_argument("--studio-url", help="本机 Studio 地址，默认 http://127.0.0.1:8765")
    args = parser.parse_args(argv)
    if args.studio_url:
        from .integrations.studio import StudioClient
        client = StudioClient(args.studio_url)
        client.close()
        os.environ["CREATOROS_STUDIO_URL"] = args.studio_url
    console = RichConsole()
    console.banner()
    if args.agent:
        run_agent(DeepSeekProvider(api_key=os.environ["DEEPSEEK_API_KEY"]), console=console)
        return
    database = Database(DATABASE_URL)
    content_run_service = ContentRunService(database)
    try:
        with content_run_service.guard:
            content_run_service.guard.assert_clean()
            upgrade_database(DATABASE_URL)
            _run_menu(console, database, content_run_service)
    except ExecutionOwnershipError as error:
        console.write(f"⚠ {error}")
        console.write("Studio 已运行时，请用 python main.py --agent 连接；旧菜单是独立运行模式。")
    finally:
        database.close()


def _run_menu(console, database, content_run_service):

    def run_agent_mode():
        provider: ModelProvider = DeepSeekProvider(
            api_key=os.environ["DEEPSEEK_API_KEY"]
        )
        run_agent(provider, console=console)

    def run_operations_mode():
        provider = DeepSeekProvider(api_key=os.environ["DEEPSEEK_API_KEY"])
        parser = OperationPlanParser(provider, ContentRepository(database))
        service = PendingOperationService(database, parser)
        PendingOperationCLI(console, service).run()

    content_repository = ContentRepository(database)

    def run_content_runs_mode():
        ContentRunCLI(console, content_run_service, content_repository).run()

    CreatorOSMenu(
        console,
        authors_loader=load_author_summaries,
        agent_runner=run_agent_mode,
        operations_runner=run_operations_mode,
        runs_runner=run_content_runs_mode,
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
