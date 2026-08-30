import json
import os

from creatoros.planning import SelectionPlan, expand_selection_plan
from creatoros.tools.creator_routing import route_hotspots


def main() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    result = route_hotspots(limit=5, top_k=3)
    if result.is_error:
        raise RuntimeError(f"真实 route_hotspots 失败：{result.error_type}: {result.content}")
    payload = json.loads(result.content)
    plan = SelectionPlan.model_validate(
        {
            "execution_mode": "preview",
            "selections": [
                {"authors": "all", "candidates": {"kind": "top_n", "top_n": 1}}
            ],
        }
    )
    assignments = expand_selection_plan(plan, payload)
    assert len(assignments) == payload["author_count"]
    for item in assignments:
        print(f"{item.display_name}: #{item.hotspot_rank} {item.title[:30]}")
    print(f"live_selection_expansion=passed assignments={len(assignments)}")


if __name__ == "__main__":
    main()
