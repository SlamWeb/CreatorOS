"""Controlled committed production receipts for scheduler tests; no producer is started."""
import json
from uuid import uuid4

from sqlalchemy import func, select

from creatoros.integrations.production_identity import task_request_id
from creatoros.runs.production_tasks import ProductionTaskInput, _digest
from creatoros.storage import ContentRepository, TopicSource
from creatoros.storage.models import (ContentAttempt, ContentRevision, ContentRun, ContentRunStatus, MediaCollection,
                                      ProductionQueueItem, ProductionTask, WriteReceipt)
from creatoros.tools.production_arguments import StartRunArgs, production_tasks_payload
from creatoros.tools.results import ToolResult


def prepare_topic(db):
    content = ContentRepository(db)
    content.create_series(series_id="isolated-series", creator_id="isolated", name="isolated-series",
                          description="", audience="", skill_name="knowledge-to-carousel")
    content.add_topic(topic_id="isolated-topic", series_id="isolated-series", title="test", source=TopicSource.MANUAL)


def submit_controlled_tasks(db, tool_call, *, context, model_requested):
    assert tool_call.name == "start_content_run" and model_requested
    request = StartRunArgs.model_validate(json.loads(tool_call.arguments))
    assert request.action == "tasks" and request.topic_id == "isolated-topic" and len(request.tasks) == 1
    tasks = production_tasks_payload(request)
    request_id = task_request_id(context.agent_session_id, context.user_request_id, context.creator_id,
                                 request.topic_id, tasks)
    task_id, run_id, revision_id, collection_id = (str(uuid4()) for _ in range(4))
    parsed = [ProductionTaskInput.model_validate(task) for task in tasks]
    canonical = [item.model_dump(mode="json", exclude={"skill_ids"} if item.skill_ids is None else set())
                 for item in parsed]
    digest = _digest(dict(topic_id=request.topic_id, tasks=canonical))
    with db.session() as session:
        assert session.get(WriteReceipt, request_id) is None
        session.add(MediaCollection(id=collection_id, creator_id=context.creator_id, topic_id=request.topic_id,
            name="隔离素材", request_id=request_id, create_digest=digest,
            run_ids_json=[], order_ids_json=[], excluded_ids_json=[]))
        session.flush()
        item = tasks[0]
        session.add(ProductionTask(id=task_id, topic_id=request.topic_id, title=item["title"], brief=item["brief"],
            position=0, collection_id=collection_id, create_request_id=request_id, kind="image",
            definition_json={}, dependency_json=[], dispatch_state="dispatched"))
        session.flush()
        session.add(ContentRun(id=run_id, topic_id=request.topic_id, task_id=task_id,
            idempotency_key=f"task:{task_id}", status=ContentRunStatus.QUEUED,
            input_snapshot_json={"creator_id": context.creator_id, "series_id": "isolated-series",
                                 "topic_id": request.topic_id}))
        session.flush()
        session.add(ContentRevision(id=revision_id, content_run_id=run_id, revision_number=1, production_input_json={}))
        session.flush()
        session.add(ProductionQueueItem(run_id=run_id, revision_id=revision_id, expected_version=1, status="pending"))
        session.add(WriteReceipt(request_id=request_id, operation="queue_topics", resource_id=request.topic_id,
            origin="agent", response_json={"creator_id": context.creator_id, "kind": "production_tasks",
                                          "request_digest": digest, "task_ids": [task_id]}))
    return ToolResult(content=json.dumps({"topic_id": request.topic_id, "creator_id": context.creator_id,
        "collection_id": collection_id, "items": [{"id": task_id, "run_id": run_id, "status": "queued",
                                                     "title": tasks[0]["title"], "kind": "image"}]}))


def assert_submission_state(test, db, expected):
    with db.session() as session:
        for model in (ProductionTask, ContentRun, ContentRevision, ProductionQueueItem, WriteReceipt):
            test.assertEqual(session.scalar(select(func.count()).select_from(model)), expected, model.__name__)
        test.assertEqual(session.scalar(select(func.count()).select_from(ContentAttempt)), 0)
        if expected:
            task = session.scalar(select(ProductionTask))
            run = session.scalar(select(ContentRun))
            queue = session.scalar(select(ProductionQueueItem))
            receipt = session.get(WriteReceipt, task.create_request_id)
            test.assertEqual((run.task_id, run.idempotency_key), (task.id, f"task:{task.id}"))
            test.assertEqual((queue.run_id, queue.status), (run.id, "pending"))
            test.assertEqual((receipt.resource_id, receipt.response_json["task_ids"]), (task.topic_id, [task.id]))
