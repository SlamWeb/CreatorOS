import math
import os

from creatoros.integrations.personclone import PersonCloneClient
from creatoros.routing.embedding import BGEEmbeddingProvider
from creatoros.routing.projection import project_profile


def main():
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    with PersonCloneClient.from_env() as client:
        documents = []
        for item in client.list_personas().get("personas", []):
            documents.extend(project_profile(client.get_routing_profile(item["author"]).profile))

    embedded = BGEEmbeddingProvider().embed_documents(documents)
    assert len(embedded) == 120
    assert {item.dimension for item in embedded} == {1024}
    norms = [math.sqrt(sum(value * value for value in item.vector)) for item in embedded]
    assert max(abs(norm - 1.0) for norm in norms) < 1e-4
    print("live_routing_embedding=passed docs=120 dimension=1024 normalized=true")


if __name__ == "__main__":
    main()
