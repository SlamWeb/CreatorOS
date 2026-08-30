from pydantic import ValidationError

from creatoros.planning import CandidateSelector, SelectionPlan


def expect_invalid(payload: dict) -> None:
    try:
        SelectionPlan.model_validate(payload)
        raise AssertionError("非法选择计划应该校验失败")
    except ValidationError:
        pass


def main() -> None:
    single = SelectionPlan.model_validate(
        {
            "execution_mode": "confirmed",
            "selections": [
                {
                    "authors": ["alice"],
                    "candidates": {"kind": "positions", "positions": [2]},
                }
            ],
        }
    )
    assert single.selections[0].candidates.positions == [2]

    shared_hotspot = SelectionPlan.model_validate(
        {
            "selections": [
                {
                    "authors": ["alice", "bob"],
                    "candidates": {"kind": "hotspot_ranks", "hotspot_ranks": [7]},
                }
            ]
        }
    )
    assert shared_hotspot.selections[0].candidates.hotspot_ranks == [7]

    all_authors = SelectionPlan.model_validate(
        {
            "execution_mode": "auto",
            "selections": [
                {
                    "authors": "all",
                    "exclude_authors": ["paused-author"],
                    "candidates": {"kind": "top_n", "top_n": 3},
                }
            ],
        }
    )
    assert all_authors.selections[0].candidates.top_n == 3

    mixed = SelectionPlan.model_validate(
        {
            "selections": [
                {"authors": ["alice"], "candidates": {"kind": "positions", "positions": [1]}},
                {"authors": ["bob"], "candidates": {"kind": "positions", "positions": [2]}},
            ]
        }
    )
    assert len(mixed.selections) == 2
    assert CandidateSelector(kind="all").kind == "all"

    expect_invalid({"selections": []})
    expect_invalid(
        {
            "selections": [
                {"authors": [], "candidates": {"kind": "positions", "positions": [1]}}
            ]
        }
    )
    expect_invalid(
        {
            "selections": [
                {
                    "authors": ["alice"],
                    "candidates": {"kind": "top_n", "top_n": 2, "positions": [1]},
                }
            ]
        }
    )
    expect_invalid(
        {
            "selections": [
                {"authors": ["alice"], "candidates": {"kind": "positions", "positions": [1]}},
            ],
            "unknown": True,
        }
    )

    print("selection_plan_smoke=passed")


if __name__ == "__main__":
    main()
