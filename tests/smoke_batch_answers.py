import asyncio
import threading

import creatoros.skills.route_and_answer.batch as batch
from creatoros.integrations.personclone import PersonaAnswer, PersonCloneError
from creatoros.planning import SelectionAssignment


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
    items = [assignment("alice", 1), assignment("bob", 2), assignment("carol", 3)]

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def ask_author(self, author, question, **kwargs):
            nonlocal active, peak
            assert kwargs == {
                "query_mode": "grounded",
                "writer_prompt": "strong_identity",
                "parent_top_k": 20,
            }
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                prompts[author] = question
                await asyncio.sleep(0.05)
                if author == "bob":
                    raise PersonCloneError("生成失败", error_type="test_error")
                return PersonaAnswer(author=author, answer=f"{author} 的回答")
            finally:
                with lock:
                    active -= 1

    previous_factory = batch._async_client_factory
    batch._async_client_factory = FakeAsyncClient
    try:
        results = asyncio.run(batch.answer_assignments(items, max_concurrency=2))
    finally:
        batch._async_client_factory = previous_factory

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
