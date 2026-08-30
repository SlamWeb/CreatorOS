import asyncio
import threading
import time

import creatoros.skills.route_and_answer.batch as batch
from creatoros.planning import SelectionAssignment
from creatoros.tools.results import ToolResult


def assignment(author: str, rank: int) -> SelectionAssignment:
    return SelectionAssignment(
        author_id=author,
        display_name=author.title(),
        queue="hot",
        position=1,
        hotspot_rank=rank,
        title=f"热点 {rank}",
        url=f"https://example.com/{rank}",
        summary=f"介绍 {rank}",
        score=0.9,
        instruction="保持作者语气",
    )


def main() -> None:
    active = 0
    peak = 0
    lock = threading.Lock()
    prompts: dict[str, str] = {}

    def fake_execute(item, context):
        nonlocal active, peak
        assert context is None
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            prompts[item.author_id] = batch.build_assignment_question(item)
            time.sleep(0.05)
            if item.author_id == "bob":
                return ToolResult("生成失败", is_error=True, error_type="test_error")
            return ToolResult(f"{item.author_id} 的回答")
        finally:
            with lock:
                active -= 1

    previous = batch._execute_assignment
    batch._execute_assignment = fake_execute
    items = [assignment("alice", 1), assignment("bob", 2), assignment("carol", 3)]
    try:
        results = asyncio.run(batch.answer_assignments(items, max_concurrency=2))
    finally:
        batch._execute_assignment = previous

    assert peak == 2
    assert [item.assignment.author_id for item in results] == ["alice", "bob", "carol"]
    assert [item.succeeded for item in results] == [True, False, True]
    assert "问题介绍：介绍 1" in prompts["alice"]
    assert "额外要求：保持作者语气" in prompts["alice"]
    assert asyncio.run(batch.answer_assignments([], max_concurrency=1)) == ()
    try:
        asyncio.run(batch.answer_assignments(items, max_concurrency=0))
        raise AssertionError("非法并发数应该失败")
    except ValueError:
        pass

    print("batch_answers_smoke=passed")


if __name__ == "__main__":
    main()
