from creatoros.discovery import HotTopic
from creatoros.routing import (
    DomainMatch,
    EmbeddedRoutePrototype,
    EmbeddingError,
    RoutePrototypeDoc,
    build_domain_query,
    rank_domain_matches,
)


def document(author_id: str, prototype_id: str, prototype_type: str, label: str):
    return RoutePrototypeDoc(
        doc_id=f"{author_id}::{prototype_type}::{prototype_id}",
        author_id=author_id,
        prototype_id=prototype_id,
        prototype_type=prototype_type,
        label=label,
        embedding_text=label,
        confidence=0.8,
        evidence_doc_ids=[],
        corpus_version="v1",
        embedding_model="test-model",
        embedding_dimension=2,
    )


def main():
    topic = HotTopic(
        rank=1,
        title="平台规则调整会影响创作者吗？",
        url="https://www.zhihu.com/question/1",
        summary="问题介绍。",
    )
    assert build_domain_query(topic) == "热点标题：平台规则调整会影响创作者吗？\n问题介绍：问题介绍。"
    assert build_domain_query(HotTopic(1, "只有标题", "https://example.com")) == "热点标题：只有标题"

    embedded = (
        EmbeddedRoutePrototype(document("alice", "a1", "domain", "平台治理"), (1.0, 0.0)),
        EmbeddedRoutePrototype(document("alice", "a2", "domain", "教育"), (0.0, 1.0)),
        EmbeddedRoutePrototype(document("bob", "b1", "domain", "创作者经济"), (0.8, 0.6)),
        EmbeddedRoutePrototype(document("ignored", "p1", "perspective", "视角"), (1.0, 0.0)),
    )
    ranked = rank_domain_matches((1.0, 0.0), embedded)
    assert [item.author_id for item in ranked] == ["alice", "bob"]
    assert isinstance(ranked[0], DomainMatch)
    assert ranked[0].prototype_id == "a1"
    assert abs(ranked[0].score - 1.0) < 1e-6
    assert rank_domain_matches((1.0, 0.0), embedded, top_k=1)[0].author_id == "alice"

    try:
        rank_domain_matches((1.0, 0.0, 0.0), embedded)
        raise AssertionError("维度不一致应该失败")
    except EmbeddingError:
        pass

    print("domain_routing_smoke=passed")


if __name__ == "__main__":
    main()
