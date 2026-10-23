"""Read-only union; approved Topics always win over their research suggestion."""
from datetime import datetime, timezone


def _timestamp(value):
    if value is None:
        # A topic can be removed between the list and the read-only time projection.
        return 0
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.replace(tzinfo=timezone.utc).timestamp() if value.tzinfo is None else value.timestamp()


def _skill_usage(snapshot, catalog, metadata, *, source):
    if snapshot.get("production_protocol") == "external-import":
        return {"source": "external", "items": []}
    composition = snapshot.get("composition") or {}
    references = ([{"id": snapshot["skill_name"]}] if snapshot.get("skill_name") else
                  [composition[role] for role in ("mind", "production") if composition.get(role)])
    items = []
    for reference in references:
        skill_id = reference.get("id")
        if not skill_id:
            continue
        if skill_id not in metadata:
            try:
                metadata[skill_id] = catalog.reference_metadata(skill_id)
            except (OSError, ValueError):
                metadata[skill_id] = {"id": skill_id, "name": reference.get("name") or "历史 Skill",
                                      "version_number": None, "version_status": None}
        items.append(metadata[skill_id])
    return {"source": source, "items": items}


def topic_library(service, queries, series_id, state="all", offset=0, limit=20, *, order="queue"):
    page = queries.list_topics(series_id, offset=0, limit=100)
    if page is None:
        return None
    queued = list(page.items)
    while len(queued) < page.page.total:
        queued.extend(queries.list_topics(series_id, offset=len(queued), limit=100).items)
    items = {t.id: {**t.model_dump(mode="json"), "selection_state": "queued"} for t in queued}
    removed = service.repository.removed_topic_ids(series_id)
    for batch in service.list(series_id):
        record = service.get(batch["id"])
        if record["status"] != "ready":
            continue
        for candidate in record["candidates"]:
            topic_id = service.topic_id(batch["id"], candidate["id"])
            if topic_id in removed:
                continue
            provenance = {"batch_id": batch["id"], "candidate_id": candidate["id"],
                          "sources": candidate["sources"], "research_angle": candidate["angle"],
                          "research_created_at": batch["created_at"]}
            if candidate.get("reference_id"):
                provenance["reference_id"] = candidate["reference_id"]
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
    rows = list(items.values())
    runs = queries.topic_production_projection([row["existing_run_id"] for row in rows if row.get("existing_run_id")])
    for row in rows:
        row["production_state"] = runs.get(row.get("existing_run_id"), {}).get("production_state", "unproduced")
    rows = [r for r in rows if state == "all" or r["selection_state"] == state or r["production_state"] == state]
    if order in {"recent", "created"}:
        activity = (queries.topic_creation_times(series_id) if order == "created"
                    else queries.topic_activity_times(series_id))
        rows.sort(key=lambda row: (
            -_timestamp(activity.get(row["id"], row.get("research_created_at"))),
            row["id"],
        ))
    page_rows = rows[offset:offset + limit]
    run_ids = [row["existing_run_id"] for row in page_rows if row.get("existing_run_id")]
    covers = queries.topic_cover_projection(run_ids)
    series = queries.get_series(series_id)
    default = {"skill_name": series.skill_name, "composition": {
        "mind": {"id": series.mind_skill_id} if series.mind_skill_id else None,
        "production": {"id": series.production_skill_id} if series.production_skill_id else None}}
    metadata = {}
    for row in page_rows:
        cover = covers.get(row.get("existing_run_id"), {})
        row["cover_url"] = cover.get("cover_url")
        row["card_count"] = cover.get("card_count")
        run = runs.get(row.get("existing_run_id"))
        row["skill_usage"] = _skill_usage(run["input"] if run else default, service.catalog,
                                          metadata, source="run" if run else "series")
    return {"items": page_rows,
            "page": {"offset": offset, "limit": limit, "total": len(rows)},
            "message": "pending=待选，不可生产；queued=已入队，执行状态及允许动作看每项字段。"
                       "序号仅对当前筛选列表有效；选择待选项使用其 batch_id/candidate_id 准备人工确认。"}
