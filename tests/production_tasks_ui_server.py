"""Task-picker UI fixtures: isolated HTTP/SQLite and E2EProducer; never paid Codex."""
from __future__ import annotations

import argparse
import os

from tests.studio_e2e_server import app, root
from tests.smoke_pair_production import install_fixture

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

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8789)
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
