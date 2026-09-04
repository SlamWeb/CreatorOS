"""Start the actual Studio against a fresh, explicitly isolated S6 QA directory."""
import argparse
from pathlib import Path
import uvicorn
import time
from creatoros.ai import DeepSeekProvider
from creatoros.operations import OperationPlanParser
import os
from creatoros.storage import ContentRepository, Database, TopicSource, upgrade_database
from creatoros.runs import ContentRunService
from creatoros.web import create_app

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--parse-delay", type=float, default=0)
    args = parser.parse_args()
    root = args.root.resolve()
    workspace_tmp = (Path(__file__).resolve().parents[1] / "tmp").resolve()
    if not root.is_relative_to(workspace_tmp):
        raise ValueError("QA directory must be inside repository tmp/")
    root.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{(root / 'creatoros.db').as_posix()}"
    upgrade_database(url)
    db = Database(url)
    repo = ContentRepository(db)
    if not repo.get_creator("s6-creator"):
        repo.create_creator(creator_id="s6-creator", display_name="隔离验收 · 面试知识实验室")
        repo.create_series(series_id="s6-series", creator_id="s6-creator", name="Agent 每日一题",
                           description="仅用于 S6 验收，不是正式运营数据", audience="Agent 初学者", skill_name="knowledge-to-carousel")
        for tid, title in (("state", "AgentState"), ("context", "AgentContext")):
            repo.add_topic(topic_id=tid, series_id="s6-series", title=title, source=TopicSource.MANUAL)
    def disabled():
        raise RuntimeError("S6 QA forbids production")
    runs = ContentRunService(db, output_root=root / "outputs", producer_factory=disabled)
    def parser_factory():
        # Explicit test latency, not a substitute for the actual DeepSeek response.
        time.sleep(max(0, min(args.parse_delay, 15)))
        return OperationPlanParser(DeepSeekProvider(api_key=os.environ["DEEPSEEK_API_KEY"],
            timeout_seconds=60, max_retries=0), repo)
    try:
        uvicorn.run(create_app(database=db, run_service=runs, operation_parser_factory=parser_factory), host="127.0.0.1", port=8765)
    finally:
        db.close()
