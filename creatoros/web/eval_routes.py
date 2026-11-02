"""The current benchmark and historical evidence are read-only."""
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from creatoros.evaluation.benchmark.store import BenchmarkStoreError


def eval_routes(store):
    router = APIRouter(prefix="/api/eval")

    def respond(action, *args):
        try:
            return JSONResponse(action(*args), headers={"Cache-Control": "no-store"})
        except BenchmarkStoreError as error:
            return JSONResponse({"error": {"code": error.code, "message": str(error)}},
                                status_code=error.status_code, headers={"Cache-Control": "no-store"})

    @router.get("")
    def overview():
        return respond(store.overview)

    @router.get("/batches/{batch_id}")
    def batch(batch_id: str):
        return respond(store.batch, batch_id)

    @router.get("/batches/{batch_id}/cases/{case_id}")
    def detail(batch_id: str, case_id: str):
        return respond(store.detail, batch_id, case_id)

    @router.get("/batches/{batch_id}/cases/{case_id}/evidence/{record_id}")
    def evidence(batch_id: str, case_id: str, record_id: str, name: str = Query(..., min_length=1, max_length=64)):
        return respond(store.evidence, batch_id, case_id, record_id, name)

    return router
