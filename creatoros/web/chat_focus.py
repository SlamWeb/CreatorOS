"""Read one selected topic, never search the account or start a worker."""
import base64
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from creatoros.runs import ContentRunError
from creatoros.runs.artifacts import validate_artifact
from creatoros.storage import Creator, Series, Topic, TopicRemoval, ContentRun, ContentRevision


class ChatContextTarget(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    series_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    topic_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    run_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    revision_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")

    @model_validator(mode="after")
    def revision_needs_run(self):
        if self.revision_id and not self.run_id:
            raise ValueError("指定内容版本时需要所属 Run。")
        return self


def _read_managed(root, path, limit):
    root, path = Path(root).absolute(), Path(path).absolute()
    if not path.is_relative_to(root) or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("outside managed files")
    cursor = path
    while True:
        if cursor.is_symlink() or (cursor.exists() and getattr(cursor.lstat(), "st_file_attributes", 0) & 0x400):
            raise ValueError("linked file")
        if cursor == root:
            break
        cursor = cursor.parent
    if path.stat().st_size > limit:
        raise ValueError("file too large")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("file too large")
    return raw


class ChatFocusBuilder:
    MAX_TEXT_CHARS = 48_000
    MAX_IMAGES = 12
    MAX_IMAGE_BYTES = 20 * 1024 * 1024

    def __init__(self, database, catalog, queries, artifacts, attachments):
        self.database, self.catalog, self.queries = database, catalog, queries
        self.artifacts, self.attachments = artifacts, attachments

    def validate(self, creator_id, target):
        target = ChatContextTarget.model_validate(target).model_dump(exclude_none=True)
        with self.database.session() as session:
            creator = session.get(Creator, creator_id) if creator_id else None
            topic, series = session.get(Topic, target["topic_id"]), session.get(Series, target["series_id"])
            if (not creator or not creator.is_active or not topic or not series or not series.is_active
                    or series.creator_id != creator_id or topic.series_id != series.id
                    or session.get(TopicRemoval, topic.id) is not None):
                raise HTTPException(404, "选中的选题已移除、停用或不属于当前账号，请重新选择。")
            run = session.get(ContentRun, target["run_id"]) if target.get("run_id") else None
            if target.get("run_id") and (not run or run.topic_id != topic.id):
                raise HTTPException(404, "所选生产记录不属于这个选题。")
            if run:
                snapshot = run.input_snapshot_json or {}
                if any(snapshot.get(key) != value for key, value in (
                        ("creator_id", creator_id), ("series_id", series.id), ("topic_id", topic.id))):
                    raise HTTPException(403, "所选生产记录的冻结归属与当前账号不一致。")
            if target.get("revision_id"):
                revision = session.get(ContentRevision, target["revision_id"])
                if not revision or revision.content_run_id != run.id:
                    raise HTTPException(404, "所选内容版本不属于这个生产记录。")
                frozen = revision.production_input_json or {}
                if any(frozen.get(key) != value for key, value in (
                        ("creator_id", creator_id), ("series_id", series.id), ("topic_id", topic.id))):
                    raise HTTPException(403, "所选内容版本的冻结归属与当前账号不一致。")
        return target

    def build(self, creator_id, session_id, target, *, image_budget=None):
        target = self.validate(creator_id, target)
        with self.database.session() as session:
            series = session.get(Series, target["series_id"])
            bindings = ({"single": series.skill_name} if series.skill_name else
                        {"mind": series.mind_skill_id, "production": series.production_skill_id})
            series_name = series.name
            if not target.get("run_id"):
                latest = session.scalar(select(ContentRun).where(ContentRun.topic_id == target["topic_id"],
                                                               ContentRun.task_id.is_(None))
                    .order_by(ContentRun.created_at.desc(), ContentRun.id.desc()).limit(1))
                if latest:
                    target["run_id"] = latest.id
            if target.get("run_id") and not target.get("revision_id"):
                run = session.get(ContentRun, target["run_id"])
                revision = session.scalar(select(ContentRevision).where(
                    ContentRevision.content_run_id == run.id,
                    ContentRevision.revision_number == run.active_revision_number))
                if revision:
                    target["revision_id"] = revision.id
        self.validate(creator_id, target)  # also check a host-resolved Run/revision
        topic = self.queries.get_topic(target["topic_id"])
        if topic is None:
            raise HTTPException(404, "选中的选题已移除，请重新选择。")
        data = {"kind": "selected_topic_materials", "target": target,
                "series": {"id": target["series_id"], "name": series_name},
                "topic": topic.model_dump(mode="json", exclude_none=True),
                "current_skills": [], "frozen_skills": [], "image_inventory": [], "omissions": [],
                "note": "本问开始时的只读资料；写操作后以最新工具回执为准。图片仅以实际载入项为证据；未载入不代表看过。"}
        images, remaining = [], self.MAX_TEXT_CHARS
        byte_budget = min(self.MAX_IMAGE_BYTES, max(0, image_budget if image_budget is not None else self.MAX_IMAGE_BYTES))

        def text(value, label):
            nonlocal remaining
            value = value or ""
            shown = value[:remaining]
            remaining -= len(shown)
            if len(shown) < len(value):
                data["omissions"].append({"source": label, "reason": "正文超出本问资料预算", "shown_chars": len(shown), "total_chars": len(value)})
            return shown

        def image(label, source, read):
            nonlocal byte_budget
            item = {"label": label, **source, "loaded": False}
            data["image_inventory"].append(item)
            if len(images) >= self.MAX_IMAGES:
                item["reason"] = "本问图片数量预算已满"
                return
            try:
                raw = read()
                if len(raw) > byte_budget:
                    item["reason"] = "本问图片字节预算不足"
                    return
                attachment = self.attachments.put(session_id, label, base64.b64encode(raw).decode("ascii"))
                if attachment["size"] > byte_budget:
                    item["reason"] = "图片格式转换后超出本问字节预算"
                    return
                images.append({"label": label, "attachment": attachment})
                byte_budget -= attachment["size"]
                item.update(loaded=True, attachment_id=attachment["id"])
            except (OSError, ValueError, TypeError, OverflowError, HTTPException, ContentRunError):
                item["reason"] = "图片缺失、变化或不满足安全读取限制"

        data["topic"]["brief"] = text(topic.brief, "topic.brief")
        # Reference images first so an eight-page product does not crowd them out.
        for role, skill_id in bindings.items():
            try:
                tree = self.catalog.list_skill_files(skill_id)
                body = self.catalog.read_skill_file(skill_id, "SKILL.md", text_only=True)
                entry = {key: tree[key] for key in ("id", "name", "display_name", "digest") if key in tree}
                entry.update(role=role, text=text(body["content"], f"current:{skill_id}/SKILL.md"))
                if body.get("digest") != tree.get("digest"):
                    raise ValueError("Skill changed while reading")
                data["current_skills"].append(entry)
                references = [item for item in tree["files"] if item["kind"] == "image"]
                for index, file in enumerate(references):
                    path = file["path"]
                    if index >= 2:
                        data["image_inventory"].append({"label": f"Skill参考 · {role} · {path}",
                            "kind": "skill_reference", "skill_id": tree["id"], "path": path,
                            "loaded": False, "reason": "每份 Skill 默认载入前两张参考，余下可定向查看"})
                        continue
                    def reference(sid=tree["id"], path=path, expected=tree["digest"]):
                        result = self.catalog.read_skill_file(sid, path)
                        if result.get("digest") != expected:
                            raise ValueError("Skill changed while reading reference")
                        return result["content"]
                    image(f"Skill参考 · {role} · {path}", {"kind": "skill_reference", "skill_id": tree["id"], "path": path},
                          reference)
            except (OSError, ValueError, TypeError, KeyError):
                data["omissions"].append({"source": f"current:{role}", "reason": "当前 Skill 不可安全读取"})

        if target.get("revision_id"):
            try:
                root, digest, frozen, saved_validation = self.artifacts.locate(target["run_id"], target["revision_id"])
                projection = self.artifacts.projection(target["run_id"], target["revision_id"])
                if not projection.get("artifact_available"):
                    raise ValueError("artifact unavailable")
                copy = projection["publish_copy"].model_dump(mode="json")
                copy["body"] = text(copy.get("body"), "production.publish_copy.body")
                data["production"] = {"run_id": target["run_id"], "revision_id": target["revision_id"],
                    "artifact_digest": digest, "topic_title": frozen.topic_title,
                    "topic_brief": text(frozen.topic_brief, "production.topic_brief"),
                    "publish_copy": copy, "card_count": len(projection["cards"]),
                    "cards": [{"order": card.order, "headline": card.headline,
                        "page_spec": text(card.page_spec, f"production.card-{card.order}.page_spec"),
                        "image_prompt": text(card.image_prompt, f"production.card-{card.order}.image_prompt")}
                        for card in projection["cards"]],
                    "reference_note": "本包参考图来自当前绑定 Skill；冻结 Skill 正文单列，不能将当前参考冒充原生产参考。"}
                for path in sorted((root / "skills").glob("*/SKILL.md")):
                    try:
                        raw = _read_managed(self.artifacts.output_root, path, 512_000)
                        data["frozen_skills"].append({"role": path.parent.name,
                            "text": text(raw.decode("utf-8"), f"frozen:{path.parent.name}/SKILL.md")})
                    except (OSError, ValueError):
                        data["omissions"].append({"source": f"frozen:{path.parent.name}/SKILL.md",
                            "reason": "冻结正文不可读取，不以当前库替代；已核验图片仍可查看"})
                if not data["frozen_skills"]:
                    data["omissions"].append({"source": "frozen_skills", "reason": "此版本没有可读取的冻结 Skill 正文"})
                checksums = {info["order"]: info.get("sha256") for info in saved_validation.get("images", [])}
                if any(not checksums.get(card.order) for card in projection["cards"]):
                    checked = validate_artifact(root, composition=frozen.composition, production_protocol=frozen.production_protocol)
                    if checked.artifact_digest != digest:
                        raise ValueError("artifact changed")
                    checksums = {info.order: info.sha256 for info in checked.images}
                for card in projection["cards"]:
                    order = card.order
                    checksum = checksums[order]
                    image(f"产物第 {order} 张", {"kind": "production", "run_id": target["run_id"],
                        "revision_id": target["revision_id"], "image_index": order},
                        lambda order=order, checksum=checksum: self.artifacts.image(target["run_id"], target["revision_id"],
                            order, digest=digest, checksum=checksum)[0])
            except (OSError, ValueError, KeyError, TypeError, StopIteration, ContentRunError):
                data["omissions"].append({"source": "production", "reason": "所选版本尚无可核验产物或文件不可读取；未重新生产"})
        for item in data["image_inventory"]:
            if not item["loaded"]:
                data["omissions"].append({"source": item["label"], "reason": item["reason"]})
        # Recheck ownership after file I/O; never deliver a transferred/deleted topic.
        self.validate(creator_id, target)
        data["loaded_image_count"] = len(images)
        return {"data": data, "images": images}
