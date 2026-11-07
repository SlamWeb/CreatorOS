"""Read-only projections of the local formal-evaluation registry."""
import sqlite3

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from creatoros.evaluation.comparison import compare_runs
from creatoros.evaluation.regression_catalog import ROOT, catalog
from creatoros.evaluation.registry import RegistryError
from .observation import clean


def eval_registry_routes(registry):
    router = APIRouter(prefix="/api/eval/v1")

    def respond(action, *args):
        try:
            return JSONResponse(clean(action(*args)), headers={"Cache-Control": "no-store"})
        except RegistryError as error:
            return JSONResponse({"error": {"code": error.code, "message": str(error)}},
                                status_code=error.status, headers={"Cache-Control": "no-store"})
        except (OSError, sqlite3.Error, ValueError, TypeError):
            return JSONResponse({"error": {"code": "registry_unavailable", "message": "评测记录暂不可用。"}},
                                status_code=503, headers={"Cache-Control": "no-store"})

    @router.get("/rounds")
    def rounds():
        return respond(registry.rounds)

    @router.get("/suites")
    def suites():
        try:
            current = catalog()
            current_suites = [{**suite, "historical": False} for suite in current["suites"]]
            old_suites, unavailable = [], []
            for version in ("v2", "v1"):
                try:
                    historical = catalog(ROOT / f"docs/agent-eval/regression-catalog-{version}/catalog.json")
                    old_suites.extend({**suite, "historical": True} for suite in historical["suites"])
                except (OSError, ValueError, TypeError, KeyError):
                    unavailable.append(version)
            historical_error = (f"历史目录暂不可用（{', '.join(unavailable)}）；历史轮次及运行记录仍可查看。"
                                if unavailable else None)
            ids = [suite["id"] for suite in current_suites + old_suites]
            if len(ids) != len(set(ids)):
                raise ValueError("Catalog suite IDs overlap")
            return JSONResponse(clean({**current, "suites": current_suites + old_suites,
                                       "historical_error": historical_error}), headers={"Cache-Control": "no-store"})
        except (OSError, ValueError, TypeError, KeyError):
            return JSONResponse({"error": {"code": "catalog_unavailable", "message": "评测集目录无法读取或校验。"}},
                                status_code=503, headers={"Cache-Control": "no-store"})

    @router.get("/suites/{suite_id}/runs")
    def suite_runs(suite_id: str):
        return respond(registry.catalog_runs, suite_id)

    @router.get("/rounds/{round_id}")
    def round_detail(round_id: str):
        return respond(registry.round_detail, round_id)

    @router.get("/compare")
    def compare(baseline_run: str, candidate_run: str):
        return respond(lambda: compare_runs(registry.get_run(baseline_run), registry.get_run(candidate_run)))

    return router
