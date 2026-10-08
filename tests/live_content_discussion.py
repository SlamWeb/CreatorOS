"""Opt-in real SDK visual discussion on isolated copies; no image generation.

python -m tests.live_content_discussion --source <verified-pack-directory> --run
"""
import argparse
import hashlib
import json
from pathlib import Path
from tempfile import mkdtemp
from time import monotonic, sleep

from fastapi.testclient import TestClient

from creatoros.config import PROJECT_ROOT
from creatoros.storage import ContentAttempt
from sqlalchemy import select
from tests.studio_review_fixtures import make_fixture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-thread", default=None)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if not args.run:
        parser.error("Add --run to authorize the low-cost real SDK call; never generates images.")
    source = args.source.resolve()
    if not source.is_relative_to(PROJECT_ROOT / "outputs"):
        parser.error("Only reuse an existing project output; no arbitrary filesystem source.")
    root = Path(mkdtemp(prefix="live-discussion-", dir=PROJECT_ROOT / "tmp"))
    db, runs, producer, app = make_fixture(root, source=source)
    producer.count = 1
    run = runs.create("review-1")
    runs.execute(run.id)  # Controlled copy-only producer, no model.
    with db.session() as session:
        for attempt in session.scalars(select(ContentAttempt)):
            attempt.producer_thread_id = args.source_thread
    report = {"root": str(root), "real_sdk": True, "fixture_producer": "copy-only", "source_thread": args.source_thread}
    print(json.dumps(report), flush=True)
    try:
        with TestClient(app) as client:
            base = f"/api/runs/{run.id}"
            before = client.get(base).json()
            revision = before["revisions"][0]
            for index, question in enumerate([
                "只看实际附图，用不超过120字说明有几格、每格主要英文词是什么，以及解释是否包含中文。不要调用任何工具，不修改、不生图。",
                "继续讨论刚才这张图：用不超过100字指出一个可以改善的教学表达问题，并说明这只是建议。不要使用任何工具。",
            ], 1):
                response = client.post(base + "/discussion", json={"request_id": f"live-discussion-{index}",
                    "revision_id": revision["id"], "artifact_digest": revision["review_digest"], "message": question})
                assert response.status_code == 202, response.text
                identifier = response.json()["id"]
                deadline = monotonic() + 360
                while monotonic() < deadline:
                    item = next(r for r in client.get(base + "/discussion").json()["items"] if r["id"] == identifier)
                    if item["status"] not in {"queued", "running"}:
                        break
                    sleep(1)
                report[f"turn_{index}"] = item
                print(json.dumps({"turn": index, "status": item["status"], "reply": item["reply"], "error": item["error"]}, ensure_ascii=False), flush=True)
                assert item["status"] == "completed", item["error"]
            assert report["turn_1"]["thread_id"] == report["turn_2"]["thread_id"]
            after = client.get(base).json()
            assert (after["version"], after["status"]) == (before["version"], before["status"])
            assert after["revisions"][0]["review_digest"] == revision["review_digest"]
            report.update(passed=True, original_run_unchanged=True, producer_calls=producer.calls)
    finally:
        (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        db.close()
    print("real_visual_discussion=passed report=" + str(root / "report.json"))


if __name__ == "__main__":
    main()
