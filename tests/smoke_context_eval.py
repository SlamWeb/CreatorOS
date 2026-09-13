"""Negative controls for task-state grading and context-only ablation."""
from copy import deepcopy

from tests.eval_context_tasks import grade, split, usage_report
from tests.context_eval_cases import assistant, user, fixture
from creatoros.integrations.topic_research import TopicResearchService


def main():
    candidate = {"id": "c2", "title": "上下文", "angle": "解释预算",
                 "sources": [{"url": "https://example.org/evidence"}]}
    batch = {"id": "a" * 32, "candidates": [{"id": "c1"}, candidate]}
    data = {"initial_operations": [], "a": batch, "b": batch}
    after = [{"id": "new", "series": "series-a", "status": "awaiting_approval", "preview": {
        "changes": [{"after_topics": [{"topic_id": TopicResearchService.topic_id(batch["id"], "c2"),
            "title": "上下文需要边界", "brief": "解释预算 https://example.org/evidence"}]}]}}]
    score = lambda rows, unchanged=True, calls=[]: all(grade("changed_mind", data, rows, unchanged, "done", calls).values())
    assert score(after)
    assert not score([])
    assert not score(after, False)
    assert not score(after, calls=[{"name": "start_content_run"}])
    assert not score(after + after)
    for key, value in [("series", "series-b"), ("status", "succeeded")]:
        wrong = deepcopy(after)
        wrong[0][key] = value
        assert not score(wrong)
    for key in ("title", "topic_id", "brief"):
        wrong = deepcopy(after)
        wrong[0]["preview"]["changes"][0]["after_topics"][0][key] = "wrong"
        assert not score(wrong)
    assert all(grade("early_constraint", data, [], True, "上下文", []).values())
    assert not all(grade("early_constraint", data, after, True, "上下文", []).values())
    assert not all(grade("early_constraint", data, [], True, "不知道", []).values())
    history = [{"role": "system", "content": "stable"}, user("任务原话"),
               assistant("x" * 8000), assistant("近期步骤")]
    cut, pin = split(history)
    assert (cut, pin) == (3, 1)
    extended = history + [assistant("y" * 8000), assistant("最新步骤")]
    assert split(extended, cut, pin) == (5, 1)
    assert not usage_report([{"sent": True, "usage": None}])["usage_complete"]
    stages, _ = fixture("restart", batch, batch, '{}')
    history = stages[0]
    call_index = next(i for i, m in enumerate(history) if m.get("tool_calls"))
    constraint_index = next(i for i, m in enumerate(history) if "不要再创建" in (m.get("content") or ""))
    assert call_index < constraint_index
    assert history[call_index]["tool_calls"][0]["arguments"] != "{}"
    print("context_eval_local=passed negative_grades cuts missing_usage")


if __name__ == "__main__":
    main()
