"""A real persisted waiting row for UI indexes; no media codec or worker needed."""
from uuid import uuid4
from sqlalchemy import select
from creatoros.storage import ContentRepository, ContentRevision, MediaCollection, ProductionTask, TopicSource
from creatoros.web.observation import encode
from creatoros.web.staged_task_projection import task_view


def seed_waiting_task(database, runs):
    prefix = uuid4().hex[:12]
    creator_id, series_id, topic_id = [f"visible-{prefix}-{name}" for name in ("account", "series", "topic")]
    image_id, waiting_id, collection_id = [uuid4().hex for _ in range(3)]
    repository = ContentRepository(database)
    repository.create_creator(creator_id=creator_id, display_name="等待任务测试账号")
    series = repository.create_series(creator_id=creator_id, series_id=series_id, name="等待任务测试栏目",
        description="只读导航验证", audience="test", skill_name="knowledge-to-carousel")
    topic = repository.add_topic(topic_id=topic_id, series_id=series_id, title="等待图片的故事选题", source=TopicSource.MANUAL)
    with database.session() as session:
        session.add(MediaCollection(id=collection_id, creator_id=creator_id, topic_id=topic_id,
            name="故事镜头素材集", request_id=prefix, create_digest="a" * 64))
    with database.session() as session:
        session.add(ProductionTask(id=image_id, topic_id=topic_id, title="尚未执行的图片", position=1,
            collection_id=collection_id, create_request_id=prefix + "-image"))
    run = runs._create(topic_id, task_id=image_id)  # Queued without Execute or a queue row.
    with database.session() as session:
        revision = session.scalar(select(ContentRevision).where(ContentRevision.content_run_id == run.id))
        waiting = ProductionTask(id=waiting_id, topic_id=topic_id, title="等待图片完成后剪辑", position=2,
            collection_id=collection_id, create_request_id=prefix + "-video", kind="video", dispatch_state="waiting",
            definition_json={"creator_id": creator_id, "series_id": series_id},
            dependency_json=[{"task_id": image_id, "run_id": run.id, "revision_id": revision.id}])
        session.add(waiting)
        session.flush()
        view = task_view(waiting, topic, series, session.get(MediaCollection, collection_id))
    return {key: view[key] for key in ("id", "title", "topic_id", "topic_url", "collection_id", "collection_url", "collection_name")} | {
        "creator_id": creator_id, "series_id": series_id, "observation_node": encode("production_task", waiting_id)}
