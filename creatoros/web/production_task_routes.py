"""One Topic, independent production tasks, existing executor and collection."""
import logging
from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import Field

from creatoros.runs.production_tasks import ProductionTaskInput
from creatoros.runs import ContentRunError
from .schemas import WriteRequest


class CreateProductionTasks(WriteRequest):
    request_id: str = Field(min_length=1, max_length=64)
    tasks: list[ProductionTaskInput] = Field(min_length=1, max_length=100)


def production_task_routes(service):
    router = APIRouter(prefix="/api/topics/{topic_id}/production-tasks")

    @router.get("")
    def list_tasks(topic_id: str):
        return JSONResponse(jsonable_encoder(service.list(topic_id)), headers={"Cache-Control": "no-store"})

    @router.post("", status_code=202)
    def create_tasks(topic_id: str, body: CreateProductionTasks, request: Request):
        try:
            result = service.create(topic_id, request_id=body.request_id,
                tasks=[item.model_dump(mode="json") for item in body.tasks],
                origin_session_id=request.headers.get("x-creatoros-agent-session"))
        except ContentRunError as error:
            if error.code != "studio_outcome_unknown":
                raise
            return unknown_receipt(topic_id, body.request_id)
        except ValueError:
            raise  # Preserve the existing explicit validation rejection.
        except Exception:
            logging.getLogger(__name__).exception("Production task submission outcome unknown")
            return unknown_receipt(topic_id, body.request_id)
        return JSONResponse(jsonable_encoder(result), status_code=202, headers={"Cache-Control": "no-store"})

    @router.get("/receipts/{request_id}")
    def get_receipt(topic_id: str, request_id: str, creator_id: str | None = None):
        return JSONResponse(jsonable_encoder(service.receipt(topic_id, request_id, creator_id=creator_id)),
                            headers={"Cache-Control": "no-store"})

    @router.post("/{task_id}/resume")
    def resume(topic_id: str, task_id: str):
        return service.control(topic_id, task_id, "resume")

    @router.get("/{task_id}/history")
    def history(topic_id: str, task_id: str):
        return JSONResponse(jsonable_encoder(service.history(topic_id, task_id)), headers={"Cache-Control": "no-store"})

    @router.post("/{task_id}/cancel")
    def cancel(topic_id: str, task_id: str):
        return service.control(topic_id, task_id, "cancel")

    @router.post("/{task_id}/remove")
    def remove(topic_id: str, task_id: str):
        return service.control(topic_id, task_id, "remove")

    return router


def unknown_receipt(topic_id, request_id):
    from urllib.parse import quote
    path = f"/api/topics/{quote(topic_id, safe='')}/production-tasks/receipts/{quote(request_id, safe='')}"
    return JSONResponse(status_code=409, headers={"Cache-Control": "no-store"}, content={"error": {
        "code": "studio_outcome_unknown", "message": "制作提交结果未确认，请查询同一请求回执，勿重复提交。",
        "topic_id": topic_id, "request_id": request_id, "receipt_url": path, "outcome_unknown": True}})
