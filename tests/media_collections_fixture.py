"""Create two isolated, locally generated and validated Runs for media E2E.

This is a deterministic fixture; it never calls a model or uses production data.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="new directory under this checkout's tmp")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent.parent
    root = args.root.resolve()
    boundary = (repo / "tmp").resolve()
    if boundary != repo.resolve() / "tmp" or root == boundary or not root.is_relative_to(boundary) or root.exists():
        parser.error("Use a new directory under this checkout's tmp")
    root.mkdir(parents=True, exist_ok=False)
    database_url = f"sqlite:///{(root / 'review.db').as_posix()}"
    os.environ.update(DATABASE_URL=database_url, PYTHON_DOTENV_DISABLED="1",
                      DEEPSEEK_API_KEY="", OPENAI_API_KEY="", CODEX_API_KEY="",
                      CODEX_HOME=str(root / "no-codex-home"), CREATOROS_CODEX_EXECUTABLE=str(root / "no-codex"),
                      CREATOROS_POWERSHELL_EXECUTABLE=str(root / "no-powershell"),
                      CREATOROS_XHS_MCP_URL="http://127.0.0.1:1/mcp",
                      CREATOROS_XHS_MCP_TOKEN="", CREATOROS_XHS_MCP_TOKEN_FILE=str(root / "no-token"),
                      CREATOROS_XHS_STAGING_ROOT=str(root / "publication-staging"))

    # make_fixture includes a module-level app import; the private DB must be
    # set before importing it, even though no web service is started here.
    from tests.studio_review_fixtures import make_fixture

    database, runs, producer, _app = make_fixture(root)
    try:
        producer.count = 2
        identifiers = []
        images = []
        for topic_id in ("review-1", "review-2"):
            run = runs.create(topic_id)
            runs.execute(run.id)
            result = runs.get(run.id)
            if result.status.value != "awaiting_approval":
                raise RuntimeError(f"Controlled media fixture failed: {result.status.value}")
            revision = runs.get_active_revision(result.id)
            directory = Path(revision.artifact_directory).resolve(strict=True)
            if not directory.is_relative_to(root / "outputs") or not revision.artifact_digest:
                raise RuntimeError("Fixture artifact directory or validation is not isolated")
            validated = (revision.validation_json or {}).get("images", [])
            if len(validated) != producer.count:
                raise RuntimeError("Fixture did not validate all local images")
            images.extend(validated)
            identifiers.append(result.id)
        checks = {"unique_runs": len(set(identifiers)) == 2,
                  "validated_images": len(images) == 4,
                  "local_producer_calls": producer.calls == 2,
                  "workers_stopped": not _app.state.background_services_started}
        if not all(checks.values()):
            raise RuntimeError(f"Fixture self-check failed: {checks}")
        fixture = {"creator_id": "review-lab", "run_ids": identifiers,
                   "source_db": str(root / "review.db"), "output_root": str(root / "outputs"),
                   "producer": "deterministic local fixture; no model", "checks": checks}
        (root / "fixture.json").write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(fixture, ensure_ascii=False), flush=True)
    finally:
        database.close()


if __name__ == "__main__":
    main()
