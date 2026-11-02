"""Session-scoped durable plan endpoints."""
from typing import Any, Literal
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from creatoros.runs import ContentRunError

class Step(BaseModel):
    id: str
    title: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    wait_for_completion: bool = False

class Create(BaseModel):
    request_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=240)
    steps: list[Step]

class Edit(BaseModel):
    expected_version: int
    steps: list[Step]
    mode: Literal['replace', 'merge'] = 'replace'
    remove_step_ids: list[str] = Field(default_factory=list, max_length=100)

class Action(BaseModel):
    expected_version: int
    action: Literal['pause', 'resume', 'retry', 'skip']
    step_id: str | None = None

def routes(service):
    router = APIRouter(prefix='/api/agent/sessions/{session_id}/plans')
    def call(fn, *args):
        try: return fn(*args)
        except ContentRunError: raise  # The app's handler preserves the precise domain error code.
        except ValueError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc
    @router.get('')
    def listing(session_id: str): return service.list(session_id)
    @router.post('')
    def create(session_id: str, body: Create, request: Request):
        return call(service.create, session_id, body.request_id, body.title,
                    [s.model_dump() for s in body.steps], str(request.base_url))
    @router.get('/{plan_id}')
    def detail(session_id: str, plan_id: str): return service.get(session_id, plan_id)
    @router.patch('/{plan_id}')
    def edit(session_id: str, plan_id: str, body: Edit):
        return call(service.edit, session_id, plan_id, body.expected_version, [s.model_dump() for s in body.steps],
                    body.mode, body.remove_step_ids)
    @router.post('/{plan_id}/actions')
    def action(session_id: str, plan_id: str, body: Action):
        return call(service.action, session_id, plan_id, body.expected_version, body.action, body.step_id)
    return router
