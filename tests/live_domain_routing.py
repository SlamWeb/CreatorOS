from creatoros.integrations.personclone import PersonCloneClient
from creatoros.integrations.zhihu import ZhihuOpenAPIClient
from creatoros.routing import (
    BGEEmbeddingProvider,
    build_domain_query,
    rank_domain_matches,
)
from creatoros.routing.projection import project_profile


def main():
    zhihu = ZhihuOpenAPIClient.from_env()
    try:
        hot_list = zhihu.get_hot_list(5)
    finally:
        zhihu.close()
    with PersonCloneClient.from_env() as personclone:
        personas = personclone.list_personas().get("personas", [])
        domain_documents = []
        for item in personas:
            profile = personclone.get_routing_profile(item["author"]).profile
            domain_documents.extend(
                doc for doc in project_profile(profile) if doc.prototype_type == "domain"
            )

    embedder = BGEEmbeddingProvider()
    embedded_domains = embedder.embed_documents(domain_documents)
    query_vectors = embedder.embed_texts(
        [build_domain_query(topic) for topic in hot_list.topics]
    )
    assert hot_list.topics
    assert embedded_domains
    assert all(item.dimension == 1024 for item in embedded_domains)

    for topic, query_vector in zip(hot_list.topics, query_vectors):
        matches = rank_domain_matches(query_vector, embedded_domains, top_k=3)
        assert matches
        print(f"#{topic.rank} {topic.title[:36]}")
        for match in matches:
            print(f"  {match.author_id} | {match.score:.4f} | {match.label}")

    print(
        "live_domain_routing=passed "
        f"hotspots={len(hot_list.topics)} domains={len(embedded_domains)}"
    )


if __name__ == "__main__":
    main()
