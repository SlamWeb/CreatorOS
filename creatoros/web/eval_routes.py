"""Eval queries remain read-only; only explicit human review writes a sidecar."""
from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from creatoros.evaluation.store import EvalStoreError


class EvalReviewRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    expected_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["passed", "failed"]
    note: str = Field(min_length=1, max_length=4000)


def eval_routes(store):
    router = APIRouter(prefix="/api/eval")

    def respond(action, *args, **kwargs):
        try:
            return JSONResponse(action(*args, **kwargs), headers={"Cache-Control": "no-store"})
        except EvalStoreError as error:
            return JSONResponse({"error": {"code": error.code, "message": str(error)}},
                                status_code=error.status_code, headers={"Cache-Control": "no-store"})

    @router.get("")
    def overview():
        return respond(store.overview)

    @router.get("/runs")
    def runs(case_id: str | None = Query(None, min_length=1, max_length=20)):
        return respond(store.runs, case_id)

    @router.get("/runs/{run_id}")
    def detail(run_id: str):
        return respond(store.detail, run_id)

    @router.get("/runs/{run_id}/evidence")
    def evidence(run_id: str, name: str = Query(..., min_length=1, max_length=512)):
        return respond(store.evidence, run_id, name)

    @router.post("/runs/{run_id}/review")
    def review(run_id: str, payload: EvalReviewRequest):
        return respond(store.review, run_id, **payload.model_dump())

    return router
