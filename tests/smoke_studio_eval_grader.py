"""Reject wrong order, identity and missing evidence without model calls."""
from copy import deepcopy
from tests.eval_studio_tasks import grade_preview


def main():
    candidates = [{"id": f"c{i}", "title": f"title{i}", "angle": f"angle{i}",
                   "sources": [{"url": f"https://example.org/{i}"}]} for i in (1, 2)]
    after = [{"topic_id": c["id"], "title": c["title"],
              "brief": c["angle"] + " " + c["sources"][0]["url"]} for c in candidates]
    grade = lambda rows: all(grade_preview(rows, candidates, lambda cid: cid).values())
    assert grade(after)
    assert not grade(list(reversed(after)))
    assert not grade(after[:1])
    for key, value in [("topic_id", "wrong"), ("title", "wrong"), ("brief", "angle1"), ("brief", None)]:
        bad = deepcopy(after)
        bad[0][key] = value
        assert not grade(bad), key
    print("studio_eval_grader=passed order_identity_title_evidence")


if __name__ == "__main__":
    main()
