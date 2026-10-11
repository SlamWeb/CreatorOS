"""No-fee isolated workbench UI preflight; expensive capabilities stay blocked."""
import argparse
from pathlib import Path
from tempfile import mkdtemp

import uvicorn

from creatoros.evaluation.workbench import WorkbenchEvaluation


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8894)
    parser.add_argument("--case", choices=("S13", "P04", "P05", "P06"), default="S13")
    args = parser.parse_args()
    host = WorkbenchEvaluation(args.case, output_root=Path(mkdtemp(prefix="creatoros-workbench-reader-")))
    # Every fee-producing action is blocked in this separate display smoke.
    host.app.state.chat.provider_factory = host.fixture.reject("model_disabled_reader_smoke")
    host.fixture.extractions.submit_merge = host.fixture.reject("merge_disabled_reader_smoke")
    host.fixture.runs.producer_factory = host.fixture.reject("production_disabled_reader_smoke")
    host.app.state.executor.submit = host.fixture.reject("execution_disabled_reader_smoke")
    host.app.state.content_discussions.submit = host.fixture.reject("discussion_disabled_reader_smoke")
    host.safe_finish({"completed": False, "posts_after_refresh": 0, "page_errors": [], "merge_posts": 0,
                      "steps": ["只读展示：没有模型请求，没有生成产物。"]})
    try:
        uvicorn.run(host.app, host="127.0.0.1", port=args.port, log_level="warning")
    finally:
        host.fixture.close()


if __name__ == "__main__":
    main()
