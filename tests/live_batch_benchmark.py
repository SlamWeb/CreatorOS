import asyncio
import json
import os
import time

from creatoros.planning import SelectionPlan, expand_selection_plan
from creatoros.skills.route_and_answer import answer_assignments
from creatoros.tools.creator_routing import route_hotspots


async def measure(assignments, concurrency: int) -> tuple[float, tuple]:
    started = time.perf_counter()
    results = await answer_assignments(assignments, max_concurrency=concurrency)
    return time.perf_counter() - started, results


def main() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    routed = route_hotspots(limit=5, top_k=1)
    if routed.is_error:
        raise RuntimeError(f"真实路由失败：{routed.error_type}: {routed.content}")
    payload = json.loads(routed.content)
    if len(payload.get("plans", [])) < 2:
        raise RuntimeError("真实路由至少需要两位可匹配作者。")
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
    serial_seconds, serial = asyncio.run(measure(assignments, concurrency=1))
    concurrent_seconds, concurrent = asyncio.run(measure(assignments, concurrency=2))
    for label, results in (("serial", serial), ("concurrent", concurrent)):
        if any(item.result.is_error for item in results):
            raise RuntimeError(f"{label} 真实回答失败：{results}")
        print(
            f"{label}: "
            + " | ".join(
                f"{item.assignment.display_name} chars={len(item.result.content)} "
                f"trace={item.result.details.get('trace_id')}"
                for item in results
            )
        )
    speedup = 1 - concurrent_seconds / serial_seconds if serial_seconds else 0.0
    print(f"serial_seconds={serial_seconds:.2f}")
    print(f"concurrent_seconds={concurrent_seconds:.2f}")
    print(f"saved_seconds={serial_seconds - concurrent_seconds:.2f}")
    print(f"speedup_percent={speedup * 100:.1f}")
    print("live_batch_benchmark=passed concurrency=1_vs_2")


if __name__ == "__main__":
    main()
