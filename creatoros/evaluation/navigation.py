"""Independent host-navigation result; never changes the raw-answer grader."""


def navigation_result(doc, observed, case_id, completed, *, expected_urls=None):
    answer = next((entry for entry in reversed(doc.get("entries", []))
                   if entry.get("kind") == "assistant" and entry.get("complete")), {})
    expected = answer.get("links", [])
    hrefs = [link["url"] for link in expected]
    checked = observed.get("checked", [])
    required = case_id in {"E06", "E08", "E09", "E10", "E11"}
    relevance = expected_urls is None or (len(hrefs) == len(expected_urls) and set(hrefs) == set(expected_urls))
    ok = (completed and relevance and (bool(expected) or not required)
          and observed.get("host_links") == expected
          and observed.get("displayed_hrefs") == hrefs
          and len(checked) == len(expected)
          and [row.get("url") for row in checked] == hrefs
          and all(row.get("clicked") is True and row.get("destination_matches") is True
                  and row.get("resource_readable") is True and row.get("creator_id") == doc.get("creator_id")
                  and ("research=" not in row.get("url", "") or row.get("research_record_visible") is True)
                  and (not row.get("url", "").startswith("/runs/") or row.get("production_record_visible") is True) for row in checked)
          and observed.get("additional_turn_posts") == 0)
    return {"protocol": observed.get("protocol", "host-links-v1"), "status": "passed" if ok else "failed",
            "expected_host_links": expected, "observed": observed, "navigation_required": required,
            "expected_task_urls": expected_urls, "relevance_status": "not_assessed" if expected_urls is None else "passed" if relevance else "failed",
            "raw_answer_preserved": True, "scope": "导航入口/点击/对象/归属/零重提；不判定模型自然语言语义"}
