"""Small read-only view of authorized work waiting before a Run exists."""
from urllib.parse import urlencode

from sqlalchemy import exists, select
from creatoros.storage import ContentRun, Creator, MediaCollection, ProductionTask, Series, Topic, TopicRemoval


PENDING_STATES = {"waiting", "needs_attention", "cancelled"}


def task_view(task, topic, series, collection=None):
    frozen = task.definition_json or {}
    if frozen.get("creator_id") != series.creator_id or frozen.get("series_id") != series.id:
        return None
    topic_url = "/?" + urlencode({"creator": series.creator_id, "series": series.id, "topic": topic.id})
    collection_owned = collection is not None and collection.creator_id == series.creator_id
    return {"id": task.id, "task_id": task.id, "creator_id": series.creator_id, "kind": "production_task", "task_kind": task.kind,
            "title": task.title, "status": task.dispatch_state, "series_id": series.id, "series_name": series.name,
            "topic_id": topic.id, "topic_title": topic.title, "url": topic_url, "topic_url": topic_url,
            "collection_id": collection.id if collection_owned else None,
            "collection_name": collection.name if collection_owned else None,
            "collection_url": "/?" + urlencode({"creator": series.creator_id, "view": "materials", "collection": collection.id})
                              if collection_owned else None,
            "run_id": None, "last_error": task.last_error,
            "depends_on": [item["task_id"] for item in (task.dependency_json or []) if isinstance(item, dict) and "task_id" in item],
            "created_at": task.created_at, "updated_at": task.updated_at, "last_activity_at": task.updated_at}


def pending_task_views(session, *, creator_id=None, series_id=None, topic_id=None, limit=500):
    query = (select(ProductionTask, Topic, Series, MediaCollection)
        .join(Topic, Topic.id == ProductionTask.topic_id).join(Series, Series.id == Topic.series_id)
        .join(Creator, Creator.id == Series.creator_id)
        .outerjoin(MediaCollection, MediaCollection.id == ProductionTask.collection_id)
        .where(Series.is_active.is_(True), Creator.is_active.is_(True),
               ProductionTask.dispatch_state.in_(PENDING_STATES),
               Topic.id.not_in(select(TopicRemoval.topic_id)),
               ~exists(select(ContentRun.id).where(ContentRun.task_id == ProductionTask.id)))
        .order_by(ProductionTask.updated_at.desc(), ProductionTask.id).limit(limit))
    for field, value in ((Series.creator_id, creator_id), (Series.id, series_id), (Topic.id, topic_id)):
        if value is not None:
            query = query.where(field == value)
    return [view for task, topic, series, collection in session.execute(query)
            if (view := task_view(task, topic, series, collection)) is not None]
