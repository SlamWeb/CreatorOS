"""One Topic, independent production tasks, existing executor and collection."""
from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import Field

from creatoros.runs.production_tasks import ProductionTaskInput
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
        result = service.create(topic_id, request_id=body.request_id,
            tasks=[item.model_dump(mode="json") for item in body.tasks],
            origin_session_id=request.headers.get("x-creatoros-agent-session"))
        return JSONResponse(jsonable_encoder(result), status_code=202, headers={"Cache-Control": "no-store"})

    return router
