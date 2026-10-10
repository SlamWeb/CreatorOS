"""Independent host-navigation result; never changes the raw-answer grader."""


def navigation_result(doc, observed, case_id, completed):
    answer = next((entry for entry in reversed(doc.get("entries", []))
                   if entry.get("kind") == "assistant" and entry.get("complete")), {})
    expected = answer.get("links", [])
    hrefs = [link["url"] for link in expected]
    checked = observed.get("checked", [])
    required = case_id in {"E06", "E08", "E09", "E10", "E11"}
    ok = (completed and (bool(expected) or not required)
          and observed.get("host_links") == expected
          and observed.get("displayed_hrefs") == hrefs
          and len(checked) == len(expected)
          and [row.get("url") for row in checked] == hrefs
          and all(row.get("clicked") is True and row.get("destination_matches") is True
                  and row.get("resource_readable") is True and row.get("creator_id") == doc.get("creator_id")
                  and ("research=" not in row.get("url", "") or row.get("research_record_visible") is True) for row in checked)
          and observed.get("additional_turn_posts") == 0)
    return {"protocol": "host-links-v1", "status": "passed" if ok else "failed",
            "expected_host_links": expected, "observed": observed, "navigation_required": required,
            "raw_answer_preserved": True, "scope": "导航入口/点击/对象/归属/零重提；不判定模型自然语言语义"}
