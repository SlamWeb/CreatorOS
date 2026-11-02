"""Task-picker UI fixtures: isolated HTTP/SQLite and E2EProducer; never paid Codex."""
from __future__ import annotations

import argparse
import os

from tests.studio_e2e_server import app, root, runs, database, E2EProducer
from tests.smoke_pair_production import install_fixture
from tests.production_gate import ProductionGates

gates = ProductionGates()


class GatedProducer(E2EProducer):
    def produce_to(self, **request):
        gates.before_produce(request)
        return super().produce_to(**request)


runs.producer_factory = GatedProducer

if os.environ.get("CREATOROS_UI_HOLD_PLAN_CONTINUATION") == "1":
    # UI-only fixture: keep notifications pending without starting an LLM.
    app.state.agent_plans.set_continuation_callback(lambda: None)


@app.post("/test/production-task-pair")
def seed_pair():
    from uuid import uuid4
    catalog = app.state.skill_installs.catalog
    fixtures = root / "production-task-pair" / uuid4().hex
    return {
        "mind": install_fixture(catalog, fixtures, "knowledge-to-storyboard-deep",
                                "https://github.com/SlamWeb/knowledge-to-storyboard", "mind"),
        "production": install_fixture(catalog, fixtures, "xiaobai",
                                      "https://github.com/SlamWeb/creatorOS-ip-skills", "production"),
    }


# This endpoint belongs only to the isolated executable and must precede the SPA catch-all.
app.router.routes.insert(0, app.router.routes.pop())


@app.post("/test/production-gates/{topic_id}")
def arm_production_gate(topic_id: str, payload: dict):
    from creatoros.storage import Topic
    with database.session() as session:
        if session.get(Topic, topic_id) is None:
            raise ValueError("Only an existing isolated topic can own a producer barrier")
    return gates.arm(topic_id, payload.get("outcome", "complete"))


@app.get("/test/production-gates/{topic_id}")
def production_gate_evidence(topic_id: str):
    from sqlalchemy import select
    from creatoros.storage import ContentRun, ContentRevision, ContentAttempt
    from creatoros.storage.models import ProductionQueueItem
    with database.session() as session:
        records = list(session.scalars(select(ContentRun).where(ContentRun.topic_id == topic_id)))
        ids = [record.id for record in records]
        attempts = list(session.scalars(select(ContentAttempt.id).join(ContentRevision)
            .where(ContentRevision.content_run_id.in_(ids)))) if ids else []
        queued = list(session.scalars(select(ProductionQueueItem.run_id)
            .where(ProductionQueueItem.run_id.in_(ids)))) if ids else []
        rows = [{"id": record.id, "task_id": record.task_id, "status": record.status.value} for record in records]
    return {**gates.snapshot(topic_id), "runs": rows, "attempt_count": len(attempts), "queue_count": len(queued),
            "tasks": app.state.production_tasks.list(topic_id)["items"]}


@app.post("/test/production-gates/{topic_id}/release")
def release_production_gate(topic_id: str):
    return gates.release(topic_id)


@app.post("/test/production-gates/{topic_id}/advance")
def advance_production_gate(topic_id: str):
    # Exercise the same stage transition the queue worker uses, at an explicit
    # observation boundary rather than relying on a fixed sleep in the test.
    gates.snapshot(topic_id)
    app.state.production_tasks.stages.advance()
    return production_gate_evidence(topic_id)


for _ in range(4):
    app.router.routes.insert(0, app.router.routes.pop())

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8789)
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
