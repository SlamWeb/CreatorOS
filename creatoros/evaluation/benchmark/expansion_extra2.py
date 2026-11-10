"""Controlled, account-bound fixtures for context and collection tasks."""
import base64
from creatoros.storage import TopicSource


def seed(world, case):
    ident = case.get("id")
    if case.get("scenario") != "extra_context_data" or ident not in {"Y01", "Y02", "Y03", "Y04"}:
        return
    if ident == "Y01":
        for series_id, name in (("extra2-first", "晨间专栏"), ("extra2-second", "夜间专栏")):
            world.content.create_series(series_id=series_id, creator_id="bench-a", name=name,
                description="隔离测试栏目", audience="", skill_name=world.variables["skill_a"])
            for n in range(1, 4):
                world.content.add_topic(topic_id=f"{series_id}-{n}", series_id=series_id,
                    title=f"{name}选题{n}", brief=f"{name}原切入点{n}", source=TopicSource.MANUAL)
    elif ident == "Y02":
        svc=world.app.state.media_collections
        cid=world.variables["collection_a"]; detail=svc.get("bench-a",cid)
        items=[item for item in detail["items"] if item["included"]]
        if len(items)!=2: raise ValueError("Expected two imported items")
        world.variables.update(extra2_item1=items[0]["id"],extra2_item2=items[1]["id"])
        svc.patch("bench-a",cid,expected_version=detail["version"],excluded_ids=[items[1]["id"]])
    elif ident == "Y03":
        from creatoros.web.chat_image_actions import import_pack, ImportRequest
        sid=world.app.state.chat.create("bench-a")["id"]
        info=world.app.state.chat.attachments.put(sid,"extra.png",base64.b64encode(world.reference).decode())
        payload=ImportRequest(series_id="bench-english",attachment_ids=[info["id"]],title="额外成品来源",
            body="隔离导入",request_id="extra2-import")
        result=import_pack(world.runs,sid,payload,[({"mime_type":"image/png"},world.reference)])
        world.variables["extra2_run"]=result["run_id"]
    elif ident == "Y04":
        for topic_id,title,brief in [("extra2-compare-a","甲场景","共同地点：车站；人物：游客。"),
                                      ("extra2-compare-b","乙场景","共同地点：车站；人物：司机。")]:
            world.content.add_topic(topic_id=topic_id,series_id="bench-english",title=title,
                brief=brief,source=TopicSource.MANUAL)
