from creatoros.discovery import HotTopic
from creatoros.planning import DailyPlan, build_daily_plans, rank_hotspots_for_author
from creatoros.routing import EmbeddedRoutePrototype, RoutePrototypeDoc


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
    topics = (
        HotTopic(1, "平台治理", "https://example.com/1"),
        HotTopic(2, "教育选择", "https://example.com/2"),
        HotTopic(3, "创作者经济", "https://example.com/3"),
    )
    vectors = ((1.0, 0.0), (0.0, 1.0), (0.8, 0.6))
    embedded = (
        EmbeddedRoutePrototype(document("alice", "a1", "domain", "平台"), (1.0, 0.0)),
        EmbeddedRoutePrototype(document("alice", "a2", "domain", "教育"), (0.0, 1.0)),
        EmbeddedRoutePrototype(document("bob", "b1", "domain", "经济"), (0.8, 0.6)),
        EmbeddedRoutePrototype(document("ignored", "p1", "perspective", "视角"), (1.0, 0.0)),
    )

    plans = build_daily_plans(topics, vectors, embedded, top_k=2)
    assert all(isinstance(plan, DailyPlan) for plan in plans)
    assert [plan.author_id for plan in plans] == ["alice", "bob"]
    assert [item.hotspot_rank for item in plans[0].hot] == [1, 2]
    assert [item.hotspot_rank for item in plans[1].hot] == [3, 1]
    assert plans[0].evergreen == () and plans[0].experiment == ()
    assert plans[0].queue("hot") == plans[0].hot
    assert rank_hotspots_for_author("alice", topics, vectors, embedded, top_k=1)[0].hotspot_rank == 1
    assert rank_hotspots_for_author("nobody", topics, vectors, embedded) == ()

    try:
        build_daily_plans(topics, vectors[:2], embedded)
        raise AssertionError("长度不一致应该失败")
    except ValueError:
        pass
    try:
        build_daily_plans(topics, vectors, embedded, top_k=0)
        raise AssertionError("非法 top_k 应该失败")
    except ValueError:
        pass

    print("content_planning_smoke=passed")


if __name__ == "__main__":
    main()
