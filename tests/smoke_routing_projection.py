from creatoros.integrations.personclone import PersonCloneClient
from creatoros.routing import RoutePrototypeDoc
from creatoros.routing.projection import project_profile


def main():
    with PersonCloneClient.from_env() as client:
        personas = client.list_personas().get("personas", [])
        total = 0
        for item in personas:
            profile = client.get_routing_profile(item["author"]).profile
            docs = project_profile(profile)
            total += len(docs)
            assert all(isinstance(doc, RoutePrototypeDoc) for doc in docs)
            assert all(doc.corpus_version == profile.corpus_version for doc in docs)
            assert {doc.prototype_type for doc in docs} == {"domain", "perspective"}
            assert all(doc.embedding_text.strip() for doc in docs)
        assert total == 120
    print("real_routing_projection=passed authors=7 docs=120")


if __name__ == "__main__":
    main()
