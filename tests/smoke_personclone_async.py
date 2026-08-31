import asyncio
import json

import httpx

from creatoros.integrations.personclone import AsyncPersonCloneClient


async def handler(request: httpx.Request) -> httpx.Response:
    assert request.method == "POST"
    assert request.url.path == "/api/chat/stream"
    body = json.loads(request.content)
    assert body == {
        "author": "alice",
        "query": "热点问题",
        "query_mode": "grounded",
        "writer_prompt": "strong_identity",
        "parent_top_k": 20,
    }
    sse = (
        'event: accepted\ndata: {"status":"accepted"}\n\n'
        'event: meta\ndata: {"trace_id":"trace-async"}\n\n'
        'event: token\ndata: {"text":"异步"}\n\n'
        'event: token\ndata: {"text":"回答"}\n\n'
        'event: done\ndata: {"answer":"异步回答完成","sources":[]}\n\n'
    )
    return httpx.Response(
        200,
        content=sse.encode("utf-8"),
        headers={"content-type": "text/event-stream"},
        request=request,
    )


async def run() -> None:
    transport = httpx.MockTransport(handler)
    async with AsyncPersonCloneClient(
        base_url="http://personclone.test",
        session_cookie="smoke-cookie",
        transport=transport,
    ) as client:
        answer = await client.ask_author("alice", "热点问题")
    assert answer.answer == "异步回答完成"
    assert answer.trace_id == "trace-async"


def main() -> None:
    asyncio.run(run())
    print("personclone_async_smoke=passed")


if __name__ == "__main__":
    main()
