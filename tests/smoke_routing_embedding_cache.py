from pathlib import Path
from tempfile import TemporaryDirectory

from creatoros.routing import RoutePrototypeDoc, RoutingEmbeddingCache


def document(*, corpus_version="v1", text="投资领域"):
    return RoutePrototypeDoc(
        doc_id="alice::domain:investing",
        author_id="alice",
        prototype_id="domain:investing",
        prototype_type="domain",
        label="投资",
        embedding_text=text,
        confidence=0.8,
        evidence_doc_ids=[],
        corpus_version=corpus_version,
        embedding_model="BAAI/bge-m3",
        embedding_dimension=2,
    )


def main():
    with TemporaryDirectory() as temporary:
        path = Path(temporary) / "routing-cache.json"
        cache = RoutingEmbeddingCache(path)
        assert cache.get(document()) is None
        cache.put(document(), (0.6, 0.8))
        cache.save()

        reloaded = RoutingEmbeddingCache(path)
        assert reloaded.get(document()) == (0.6, 0.8)
        assert reloaded.get(document(corpus_version="v2")) is None
        assert reloaded.get(document(text="投资和风险领域")) is None

        reloaded.put(document(corpus_version="v2"), (1.0, 0.0))
        reloaded.save()
        assert RoutingEmbeddingCache(path).get(document(corpus_version="v2")) == (1.0, 0.0)

        path.write_text("not json", encoding="utf-8")
        assert RoutingEmbeddingCache(path).get(document()) is None
    print("routing_embedding_cache_smoke=passed")


if __name__ == "__main__":
    main()
