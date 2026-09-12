import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool


class ChatTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(pattern=r"^[a-f0-9-]{36}$")
    text: str = Field(min_length=1, max_length=8000)
    expected_version: int = Field(ge=0)


def chat_routes(service):
    router = APIRouter(prefix="/api/agent/sessions")

    @router.get("")
    def sessions():
        return {"items": service.list()}

    @router.post("", status_code=201)
    def create():
        return service.create()

    @router.get("/{session_id}")
    def get(session_id: UUID):
        return service.get(str(session_id))

    @router.get("/{session_id}/context-trace")
    def context_trace(session_id: UUID, after: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
        return service.context_trace(str(session_id), after, limit)

    @router.post("/{session_id}/turns", status_code=202)
    def submit(session_id: UUID, payload: ChatTurnRequest, request: Request):
        text = payload.text.strip()
        if not text or text.startswith("/"):
            raise HTTPException(422, "请输入自然语言；网页不执行 CLI 斜杠命令。")
        # Use the actual listener, never a Host header or shared process env mutation.
        host, port = request.scope.get("server") or (None, None)
        if host not in {"127.0.0.1", "localhost", "::1"} or not port:
            raise HTTPException(503, "Web Agent 需要本机 Studio 监听地址。")
        base = f"http://{'[' + host + ']' if ':' in host else host}:{port}"
        return service.submit(str(session_id), payload.request_id, text, payload.expected_version, base)

    @router.get("/{session_id}/events")
    async def events(session_id: UUID, request: Request):
        sid = str(session_id)
        await run_in_threadpool(service.get, sid)

        async def stream():
            previous = None
            heartbeat = 0
            while not getattr(request.app.state, "stop_observers", False) and not await request.is_disconnected():
                view = await run_in_threadpool(service.get, sid)
                serialized = json.dumps(view, ensure_ascii=False)
                if serialized != previous:
                    yield f"event: snapshot\ndata: {serialized}\n\n"
                    previous = serialized
                heartbeat += 1
                if heartbeat % 30 == 0:
                    yield ": keepalive\n\n"
                await asyncio.sleep(0.5)
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return router
