from creatoros.planning import SelectionExpansionError, SelectionPlan, expand_selection_plan


def candidate(position: int, rank: int, title: str) -> dict:
    return {
        "position": position,
        "rank": rank,
        "title": title,
        "url": f"https://example.com/{rank}",
        "summary": f"{title}介绍",
        "score": 1 - rank / 100,
    }


ROUTES = {
    "plans": [
        {
            "author_id": "alice",
            "display_name": "Alice",
            "hot": [candidate(1, 7, "共同热点"), candidate(2, 2, "Alice热点")],
            "evergreen": [],
            "experiment": [],
        },
        {
            "author_id": "bob",
            "display_name": "Bob",
            "hot": [candidate(1, 3, "Bob热点"), candidate(2, 7, "共同热点")],
            "evergreen": [],
            "experiment": [],
        },
        {
            "author_id": "paused",
            "display_name": "Paused",
            "hot": [candidate(1, 9, "暂停作者热点")],
            "evergreen": [],
            "experiment": [],
        },
    ]
}


def plan(selections: list[dict], **extra) -> SelectionPlan:
    return SelectionPlan.model_validate({"selections": selections, **extra})


def expect_error(value: SelectionPlan, text: str) -> None:
    try:
        expand_selection_plan(value, ROUTES)
        raise AssertionError("无法展开的选择应该失败")
    except SelectionExpansionError as error:
        assert text in str(error)


def main() -> None:
    one = expand_selection_plan(
        plan([{"authors": ["alice"], "candidates": {"kind": "positions", "positions": [2]}}]),
        ROUTES,
    )
    assert [(item.author_id, item.hotspot_rank) for item in one] == [("alice", 2)]

    shared = expand_selection_plan(
        plan([{"authors": ["alice", "bob"], "candidates": {"kind": "hotspot_ranks", "hotspot_ranks": [7]}}]),
        ROUTES,
    )
    assert [(item.author_id, item.position) for item in shared] == [("alice", 1), ("bob", 2)]

    top_one = expand_selection_plan(
        plan(
            [{"authors": "all", "exclude_authors": ["paused"], "candidates": {"kind": "top_n", "top_n": 1}}],
            instruction="保留作者自己的语气",
        ),
        ROUTES,
    )
    assert [item.author_id for item in top_one] == ["alice", "bob"]
    assert all(item.instruction == "保留作者自己的语气" for item in top_one)

    deduplicated = expand_selection_plan(
        plan(
            [
                {"authors": ["alice"], "candidates": {"kind": "positions", "positions": [1]}},
                {"authors": ["alice"], "candidates": {"kind": "hotspot_ranks", "hotspot_ranks": [7]}},
            ]
        ),
        ROUTES,
    )
    assert len(deduplicated) == 1

    expect_error(
        plan([{"authors": ["unknown"], "candidates": {"kind": "all"}}]),
        "未知作者",
    )
    expect_error(
        plan([{"authors": ["alice"], "candidates": {"kind": "positions", "positions": [99]}}]),
        "不存在 position",
    )
    expect_error(
        plan([{"authors": ["alice"], "queue": "evergreen", "candidates": {"kind": "all"}}]),
        "没有符合条件",
    )

    print("selection_expansion_smoke=passed")


if __name__ == "__main__":
    main()
