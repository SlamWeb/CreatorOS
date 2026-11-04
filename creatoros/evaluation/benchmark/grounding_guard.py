"""Do not certify research-source claims without captured source contents."""
from copy import deepcopy

VERSION = "research-grounding-qualification-v1"


def qualify_grounding_judgments(case, judge):
    result = deepcopy(judge)
    if case.get("id") != "B48":
        return result
    # The current collector records the batch and model's descriptions, but
    # not independently captured source pages tied to the asserted meanings.
    for item in result.get("items", []):
        if item.get("id") == "b48-s1" and item.get("pass") is not None:
            item["pass"] = None
            item["qualification"] = VERSION
            item["evidence"] += "；未采到可独立核验的检索来源正文与词义对应，待复核。"
    return result
