import os

from creatoros.integrations.personclone import PersonCloneClient
from creatoros.integrations.zhihu import ZhihuOpenAPIClient
from creatoros.planning import build_daily_plans
from creatoros.routing import BGEEmbeddingProvider, build_domain_query
from creatoros.routing.projection import project_profile


def main():
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    zhihu = ZhihuOpenAPIClient.from_env()
    try:
        hot_list = zhihu.get_hot_list(5)
    finally:
        zhihu.close()

    with PersonCloneClient.from_env() as personclone:
        personas = personclone.list_personas().get("personas", [])
        documents = []
        for item in personas:
            profile = personclone.get_routing_profile(item["author"]).profile
            documents.extend(
                doc for doc in project_profile(profile) if doc.prototype_type == "domain"
            )

    embedder = BGEEmbeddingProvider()
    embedded_domains = embedder.embed_documents(documents)
    query_vectors = embedder.embed_texts(
        [build_domain_query(topic) for topic in hot_list.topics]
    )
    plans = build_daily_plans(
        hot_list.topics,
        query_vectors,
        embedded_domains,
        top_k=3,
    )
    assert len(plans) == len({item.document.author_id for item in embedded_domains})
    assert all(plan.hot for plan in plans)
    for plan in plans:
        print(f"{plan.author_id}: " + " | ".join(
            f"#{item.hotspot_rank} {item.score:.4f} {item.hotspot_title[:24]}"
            for item in plan.hot
        ))
    print(
        "live_content_planning=passed "
        f"authors={len(plans)} hotspots={len(hot_list.topics)} domains={len(embedded_domains)}"
    )


if __name__ == "__main__":
    main()
