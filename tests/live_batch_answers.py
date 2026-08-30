import asyncio
import json
import os

from creatoros.planning import SelectionPlan, expand_selection_plan
from creatoros.skills.route_and_answer import answer_assignments
from creatoros.tools.creator_routing import route_hotspots


def main() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    routed = route_hotspots(limit=5, top_k=1)
    if routed.is_error:
        raise RuntimeError(f"真实路由失败：{routed.error_type}: {routed.content}")
    payload = json.loads(routed.content)
    author_ids = [item["author_id"] for item in payload["plans"][:2]]
    plan = SelectionPlan.model_validate(
        {
            "execution_mode": "confirmed",
            "selections": [
                {"authors": author_ids, "candidates": {"kind": "positions", "positions": [1]}}
            ],
        }
    )
    assignments = expand_selection_plan(plan, payload)
    results = asyncio.run(answer_assignments(assignments, max_concurrency=2))
    assert len(results) == 2
    for item in results:
        if not item.succeeded:
            raise RuntimeError(
                f"真实回答失败：{item.assignment.author_id}: "
                f"{item.result.error_type}: {item.result.content}"
            )
        print(
            f"{item.assignment.display_name}: answer_chars={len(item.result.content)} "
            f"trace_id={item.result.details.get('trace_id')}"
        )
    print("live_batch_answers=passed count=2 max_concurrency=2")


if __name__ == "__main__":
    main()
