import json

import httpx

from creatoros.integrations.personclone import PersonCloneClient
from creatoros.tools import tool_registry


def main():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path == "/api/personas":
            return httpx.Response(
                200,
                json={
                    "default_author": "alice",
                    "personas": [
                        {
                            "author": "alice",
                            "display_name": "Alice",
                            "index_dir": "secret/internal/path",
                            "narrative_schema_available": True,
                        }
                    ],
                },
                request=request,
            )
        if request.method == "GET" and request.url.path == "/api/personas/alice/routing-profile":
            return httpx.Response(
                200,
                json={
                    "status": "reused",
                    "profile": {
                        "author_id": "alice",
                        "status": "ready",
                        "corpus_version": "corpus-v1",
                        "domain_prototypes": [],
                        "perspective_prototypes": [],
                    },
                },
                request=request,
            )
        if request.method == "POST" and request.url.path == "/api/author-jobs":
            body = json.loads(request.content)
            assert body == {"author": "https://www.zhihu.com/people/alice", "kinds": ["answer"]}
            return httpx.Response(200, json={"id": "job-1", "status": "queued"}, request=request)
        if request.method == "POST" and request.url.path == "/api/chat/stream":
            body = json.loads(request.content)
            assert body["author"] == "alice"
            assert body["query"] == "热点问题"
            assert body["query_mode"] == "grounded"
            assert body["writer_prompt"] == "strong_identity"
            assert body["parent_top_k"] == 20
            sse = (
                'event: meta\ndata: {"trace_id":"trace-1"}\n\n'
                'event: token\ndata: {"text":"回答"}\n\n'
                'event: done\ndata: {"answer":"回答完成","sources":[]}\n\n'
            )
            return httpx.Response(
                200,
                content=sse.encode("utf-8"),
                headers={"content-type": "text/event-stream"},
                request=request,
            )
        return httpx.Response(404, json={"detail": "not found"}, request=request)

    client = PersonCloneClient(
        base_url="http://personclone.test",
        session_cookie="smoke-cookie",
        transport=httpx.MockTransport(handler),
    )
    try:
        personas = client.list_personas()
        assert personas["personas"][0]["author"] == "alice"

        routing = client.get_routing_profile("alice")
        assert routing["profile"]["status"] == "ready"
        assert routing["profile"]["corpus_version"] == "corpus-v1"

        job = client.add_author(
            "https://www.zhihu.com/people/alice",
            ["answer"],
        )
        assert job == {"id": "job-1", "status": "queued"}

        answer = client.ask_author("alice", "热点问题")
        assert answer.answer == "回答完成"
        assert answer.trace_id == "trace-1"
    finally:
        client.close()

    assert {"list_authors", "add_author", "ask_author"}.issubset(tool_registry)
    assert "author" in tool_registry["ask_author"].to_schema()["function"]["parameters"]["properties"]
    assert all(request.headers.get("cookie") == "personaforge_session=smoke-cookie" for request in requests)
    print("personclone_smoke=passed")


if __name__ == "__main__":
    main()
