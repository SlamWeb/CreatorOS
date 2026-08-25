from pydantic import ValidationError

from creatoros.routing import RoutingProfileEnvelope


def evidence(**overrides):
    values = {
        "doc_id": "doc-1",
        "title": "标题",
        "kind": "answer",
        "updated_at": "2026-08-26T00:00:00Z",
        "source_method": "title_cluster",
        "field": None,
        "claim_id": None,
        "excerpt": None,
    }
    values.update(overrides)
    return values


def vector_ref():
    return {
        "collection_name": "creator_routing_profiles",
        "point_id": "point-1",
        "vector_name": "dense",
        "embedding_model": "BAAI/bge-m3",
        "dimension": 1024,
        "normalized": True,
        "corpus_version": "corpus-v1",
    }


def profile_payload(status="ready"):
    return {
        "status": "reused",
        "profile": {
            "schema_version": 1,
            "author_id": "alice",
            "display_name": "Alice",
            "source": "zhihu",
            "generated_at": "2026-08-26T00:00:00Z",
            "corpus_version": "corpus-v1",
            "config_hash": "config-v1",
            "status": status,
            "embedding_model": "BAAI/bge-m3",
            "embedding_dimension": 1024,
            "qdrant_collection": "creator_routing_profiles",
            "domain_prototypes": [
                {
                    "prototype_id": "domain:1",
                    "label": "领域",
                    "description": "领域描述",
                    "retrieval_text": "领域检索文本",
                    "document_count": 3,
                    "representative_evidence": [evidence()],
                    "confidence": 0.82,
                    "status": "stable",
                    "vector_ref": vector_ref(),
                }
            ],
            "perspective_prototypes": [
                {
                    "prototype_id": "perspective:1",
                    "label": "先拆变量再判断",
                    "summary": "视角摘要",
                    "values": ["证据边界"],
                    "trigger_cues": ["风险"],
                    "reasoning_pattern": "先拆解再判断",
                    "boundaries": ["不把短期现象说成趋势"],
                    "retrieval_text": "视角检索文本",
                    "representative_evidence": [evidence(field="values", claim_id="claim-1", excerpt="证据")],
                    "confidence": 0.85,
                    "source_method": "narrative_schema",
                    "vector_ref": vector_ref(),
                }
            ],
        },
    }


def main():
    ready = RoutingProfileEnvelope.model_validate(profile_payload())
    assert ready.profile.can_use_domain
    assert ready.profile.can_use_perspective
    assert ready.profile.domain_prototypes[0].representative_evidence[0].excerpt is None

    domain_only = RoutingProfileEnvelope.model_validate(profile_payload("domain_ready"))
    assert domain_only.profile.can_use_domain
    assert not domain_only.profile.can_use_perspective

    invalid = profile_payload()
    invalid["profile"]["unexpected"] = True
    try:
        RoutingProfileEnvelope.model_validate(invalid)
        raise AssertionError("额外字段应该被拒绝")
    except ValidationError:
        pass

    print("routing_models_smoke=passed")


if __name__ == "__main__":
    main()
