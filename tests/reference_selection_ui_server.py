"""One-off reference Skill selection: isolated SQLite/files and local E2EProducer."""
import argparse
from uuid import uuid4

from tests.studio_e2e_server import app, root, database
from creatoros.storage import ContentRepository


@app.post("/test/reference-selection")
def seed():
    key = uuid4().hex
    repo = ContentRepository(database)
    creator = repo.create_creator(creator_id="reference-" + key, display_name="参考制作隔离账号")
    series = repo.create_series(series_id="reference-series-" + key, name="本次精确 Skill", creator_id=creator.id,
                                description="", audience="", skill_name=None)
    library = app.state.skill_library
    skills = []
    for index in range(2):
        directory = root / "reference-selection" / key / str(index)
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(f"---\nname: reference-{index}-{key}\ndescription: controlled reference selection\n---\nExact rules {index}.\n", encoding="utf-8")
        skill = library.catalog.register_local(directory, role="production")
        library.add(creator.id, skill["id"], f"library-{key}-{index}")
        skills.append(skill)
    pool = library.set_series(series.id, 0, [skill["id"] for skill in skills], {"image": [], "video": []})
    return {"creator": creator.id, "series": series.id, "skills": skills, "pool": pool}


app.router.routes.insert(0, app.router.routes.pop())


@app.get("/test/reference-selection/{series_id}")
def evidence(series_id: str):
    from sqlalchemy import select
    from creatoros.storage import Topic, ContentRun, ContentRevision, ContentAttempt
    from creatoros.storage.models import ProductionQueueItem
    with database.session() as session:
        topics = list(session.scalars(select(Topic.id).where(Topic.series_id == series_id)))
        runs = list(session.scalars(select(ContentRun.id).where(ContentRun.topic_id.in_(topics))))
        revisions = list(session.scalars(select(ContentRevision.id).where(ContentRevision.content_run_id.in_(runs))))
        attempts = list(session.scalars(select(ContentAttempt.id).where(ContentAttempt.revision_id.in_(revisions))))
        queued = list(session.scalars(select(ProductionQueueItem.id).where(ProductionQueueItem.run_id.in_(runs))))
    return {"topics": topics, "runs": runs, "attempt_count": len(attempts), "queue_count": len(queued)}


app.router.routes.insert(0, app.router.routes.pop())

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
