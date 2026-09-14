"""Read-only union; approved Topics always win over their research suggestion."""


def topic_library(service, queries, series_id, state="all", offset=0, limit=20):
    page = queries.list_topics(series_id, offset=0, limit=100)
    if page is None:
        return None
    queued = list(page.items)
    while len(queued) < page.page.total:
        queued.extend(queries.list_topics(series_id, offset=len(queued), limit=100).items)
    items = {t.id: {**t.model_dump(mode="json"), "selection_state": "queued"} for t in queued}
    for batch in service.list(series_id):
        record = service.get(batch["id"])
        if record["status"] != "ready":
            continue
        for candidate in record["candidates"]:
            topic_id = service.topic_id(batch["id"], candidate["id"])
            provenance = {"batch_id": batch["id"], "candidate_id": candidate["id"],
                          "sources": candidate["sources"], "research_angle": candidate["angle"],
                          "research_created_at": batch["created_at"]}
            if topic_id in items:
                items[topic_id].update(provenance)
                continue
            items[topic_id] = {
                "id": topic_id, "series_id": series_id, "title": candidate["title"],
                "selection_state": "pending", "status": "pending_selection",
                "angle": candidate["angle"], "rationale": candidate["rationale"],
                "stale": record["stale"], "source": "research", **provenance,
                "available_actions": [] if record["stale"] else ["prepare_topic_selection"],
            }
    rows = [r for r in items.values() if state == "all" or r["selection_state"] == state]
    return {"items": rows[offset:offset + limit],
            "page": {"offset": offset, "limit": limit, "total": len(rows)},
            "message": "pending=待选，不可生产；queued=已入队，执行状态及允许动作看每项字段。"
                       "序号仅对当前筛选列表有效；选择待选项使用其 batch_id/candidate_id 准备人工确认。"}
