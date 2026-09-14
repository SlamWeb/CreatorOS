"""Local-only dataset and grader positive/negative controls; no model calls."""
from copy import deepcopy

from tests.operating_eval_cases import CASES, validate_cases
from tests.operating_eval_grader import grade_preview


def main():
    assert validate_cases() and len(CASES) == 12
    case = CASES[0]
    expected = [{"id": "research-batch-c2", "title": "标题二", "brief": "独特角度二 https://example.org/source-two"}]
    before_ops = [{"id": "old-op", "series": "series-x", "status": "awaiting_approval", "preview": {}}]
    after_ops = before_ops + [{"id": "new-op", "series": "series-x", "status": "awaiting_approval",
        "preview": {"changes": [{"after_topics": [{"topic_id": expected[0]["id"], "title": expected[0]["title"], "brief": expected[0]["brief"]}]}]}}]
    kwargs = dict(before_operations=before_ops, after_operations=after_ops, before_topics=[], after_topics=[],
                  calls=[{"name": "list_series_topics"}, {"name": "prepare_topic_selection"}],
                  expected_topics=expected, series_id="series-x")
    assert all(grade_preview(case, **kwargs).values())
    for mutate in (
        lambda x: x["after_operations"].append({"id": "extra", "series": "series-x", "status": "awaiting_approval", "preview": {}}),
        lambda x: x["after_operations"][-1]["preview"]["changes"][0]["after_topics"][0].update(topic_id="wrong"),
        lambda x: x["after_operations"][-1]["preview"]["changes"][0]["after_topics"][0].update(title="wrong"),
        lambda x: x["after_operations"][-1]["preview"]["changes"][0]["after_topics"][0].update(brief="wrong"),
        lambda x: x["after_operations"][-1].update(series="wrong"),
        lambda x: x["after_operations"][-1].update(status="cancelled"),
        lambda x: x["calls"].append({"name": "start_content_run"}),
        lambda x: x["after_topics"].append({"id": "unauthorized-topic"}),
    ):
        broken = deepcopy(kwargs)
        mutate(broken)
        assert not all(grade_preview(case, **broken).values())
    empty = deepcopy(kwargs)
    empty["after_operations"] = before_ops
    assert not all(grade_preview(case, **empty).values())
    read_case = next(c for c in CASES if c["goal"] == "read_only")
    read = grade_preview(read_case, **{**kwargs, "after_operations": before_ops, "expected_topics": [], "calls": [{"name": "get_topic_research"}]})
    assert read["no_preview"] and read["queried_candidates"]
    read_bad = grade_preview(read_case, **{**kwargs, "expected_topics": [], "calls": [{"name": "start_content_run"}]})
    assert not read_bad["no_forbidden_attempt"]
    clarify = next(c for c in CASES if c["goal"] == "clarify")
    assert grade_preview(clarify, **{**kwargs, "after_operations": before_ops, "expected_topics": [], "final_answer": "请问你指哪个账号？"})["no_unambiguous_guess"]
    print("operating_eval_smoke=passed cases=12 positive_and_negative_controls=passed")


if __name__ == "__main__":
    main()
