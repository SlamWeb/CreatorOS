import json
from pathlib import Path
from tempfile import TemporaryDirectory

import creatoros.tools.creator_routing as creator_routing_tools
from creatoros.ai.types import ToolCall
from creatoros.discovery import HotListSnapshot, HotTopic
from creatoros.routing import (
    AuthorRoutingProfile,
    DomainPrototype,
    EmbeddedRoutePrototype,
    RoutingProfileEnvelope,
    RoutingEmbeddingCache,
    VectorRef,
)
from creatoros.tools.definitions import tool_registry
from creatoros.tools.execution import execute_tool_call


def profile(author_id: str, label: str) -> RoutingProfileEnvelope:
    prototype = DomainPrototype(
        prototype_id=f"domain:{author_id}",
        label=label,
        description=f"{label} 领域",
        retrieval_text=label,
        document_count=3,
        representative_evidence=[],
        confidence=0.8,
        status="stable",
        vector_ref=VectorRef(
            collection_name="creator_routing_profiles",
            point_id=f"point-{author_id}",
            vector_name="dense",
            embedding_model="BAAI/bge-m3",
            dimension=2,
            normalized=True,
            corpus_version="v1",
        ),
    )
    value = AuthorRoutingProfile(
        schema_version=1,
        author_id=author_id,
        display_name=author_id.title(),
        source="zhihu",
        generated_at="2026-08-28T00:00:00Z",
        corpus_version="v1",
        config_hash="config-v1",
        status="ready",
        embedding_model="BAAI/bge-m3",
        embedding_dimension=2,
        qdrant_collection="creator_routing_profiles",
        domain_prototypes=[prototype],
        perspective_prototypes=[],
    )
    return RoutingProfileEnvelope(status="reused", profile=value)


class FakeZhihuClient:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    def get_hot_list(self, limit):
        assert limit == 2
        return HotListSnapshot(
            source="zhihu",
            total=2,
            topics=(
                HotTopic(1, "平台治理", "https://example.com/1", "规则变化"),
                HotTopic(2, "教育选择", "https://example.com/2", "升学问题"),
            ),
        )


class FakePersonCloneClient:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    def list_personas(self):
        return {
            "personas": [
                {"author": "alice", "display_name": "Alice"},
                {"author": "bob", "display_name": "Bob"},
            ]
        }

    def get_routing_profile(self, author):
        return profile(author, "平台治理" if author == "alice" else "教育选择")


class FakeEmbeddingProvider:
    def embed_documents(self, documents):
        return tuple(
            EmbeddedRoutePrototype(
                document=document,
                vector=(1.0, 0.0) if document.author_id == "alice" else (0.0, 1.0),
            )
            for document in documents
        )

    def embed_texts(self, texts):
        assert len(texts) == 2
        return ((1.0, 0.0), (0.0, 1.0))


def main():
    zhihu_client = FakeZhihuClient()
    personclone_client = FakePersonCloneClient()
    previous_zhihu = creator_routing_tools._zhihu_client_factory
    previous_personclone = creator_routing_tools._personclone_client_factory
    previous_embedder = creator_routing_tools.BGEEmbeddingProvider
    previous_cache = creator_routing_tools._embedding_cache_factory
    creator_routing_tools._zhihu_client_factory = lambda: zhihu_client
    creator_routing_tools._personclone_client_factory = lambda: personclone_client
    creator_routing_tools.BGEEmbeddingProvider = FakeEmbeddingProvider
    with TemporaryDirectory() as temporary:
        creator_routing_tools._embedding_cache_factory = lambda: RoutingEmbeddingCache(
            Path(temporary) / "routing-cache.json"
        )
        try:
            result = execute_tool_call(
                ToolCall("route-1", "route_hotspots", json.dumps({"limit": 2, "top_k": 1}))
            )
        finally:
            creator_routing_tools._zhihu_client_factory = previous_zhihu
            creator_routing_tools._personclone_client_factory = previous_personclone
            creator_routing_tools.BGEEmbeddingProvider = previous_embedder
            creator_routing_tools._embedding_cache_factory = previous_cache

    payload = json.loads(result.content)
    assert not result.is_error
    assert payload["matching"] == "domain_max_similarity"
    assert payload["hotspot_count"] == 2
    assert payload["author_count"] == 2
    assert [item["title"] for item in payload["plans"][0]["hot"]] == ["平台治理"]
    assert [item["title"] for item in payload["plans"][1]["hot"]] == ["教育选择"]
    assert result.details == {
        "hotspot_count": 2,
        "author_count": 2,
        "top_k": 1,
        "skipped_author_count": 0,
        "prototype_cache_hits": 0,
        "prototype_cache_misses": 2,
    }
    assert zhihu_client.closed and personclone_client.closed
    assert "route_hotspots" in tool_registry
    assert "limit" in tool_registry["route_hotspots"].to_schema()["function"]["parameters"]["properties"]
    print("route_hotspots_tool_smoke=passed")


if __name__ == "__main__":
    main()
