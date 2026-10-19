"""Thin model-facing adapters over the same Studio API used by the browser."""
import json
from hashlib import sha256
from typing import Literal
from urllib.parse import quote
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..integrations.studio import StudioClient, StudioClientError
from .results import ToolResult
from ..integrations.topic_research import CandidateSelection
from .research_wait import observe_research


class ResearchTopicsArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    series_id: str = Field(min_length=1, description="真实栏目 ID。使用当前栏目定位、受众和绑定 Skill。")
    count: int = Field(default=10, ge=1, le=30, description="最多候选数；允许调研不足，不凑数。")
    instructions: str = Field(default="", max_length=3000, description="本次选题偏好、排除方向等用户要求。")
    attachment_ids: list[str] = Field(default_factory=list, max_length=20, description="用户提供的当前会话参考图片ID；原图与要求一起交给Codex，不用图片路径或凭空猜ID。")
    reference_presentation: bool = Field(default=False, description="默认只借鉴图片内容；用户明确要求借鉴画面风格/版式时才设为true。")


class ResearchBatchArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    batch_id: str = Field(pattern=r"^[a-f0-9]{32}$", description="调研返回的真实批次 ID。")


class SelectResearchArgs(ResearchBatchArgs):
    selections: list[CandidateSelection] = Field(min_length=1, max_length=30,
        description="按用户要求的入队顺序列出候选 ID；可改 title/angle，省略则保留原文。只生成 Preview，不确认。")


def research_series_topics(series_id, count=10, instructions="", context=None, attachment_ids=None, reference_presentation=False):
    def research(client):
        reference_id = _prepare_task_reference(client, context, series_id, attachment_ids, instructions, reference_presentation)
        batch = client.request("POST", f"/api/series/{quote(series_id, safe='')}/topic-research",
                               payload={"count": count, "instructions": instructions,
                                        **({"reference_id": reference_id} if reference_id else {})})
        return observe_research(client, batch, context)
    return _call(research, context)


def get_topic_research(batch_id, context=None):
    return _call(lambda c: observe_research(c, c.request("GET", f"/api/topic-research/{batch_id}"), context), context)


def prepare_topic_selection(batch_id, selections, context=None):
    items = [s.model_dump() if isinstance(s, CandidateSelection) else s for s in selections]
    def preview(client):
        data = client.request("POST", f"/api/topic-research/{batch_id}/preview", payload={"selections": items})
        return {"operation_id": data["id"], "series_id": data['scope_series_id'], "batch_id": batch_id,
                "status": data["status"], "preview": data["preview"],
                "url": f"/series/{data['scope_series_id']}?research={batch_id}&operation={data['id']}",
                "message": "仅生成待确认计划，尚未入队；请用户打开链接验收并确认。"}
    return _call(preview, context)


class PageArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    offset: int = Field(default=0, ge=0, description="分页起点；还有数据时继续翻页。")
    limit: int = Field(default=20, ge=1, le=100, description="每页数量，最多 100。")


class CreatorArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    creator_id: str = Field(min_length=1, description="从 list_creators 取得的真实账号 ID。")


class TopicsArgs(PageArgs):
    series_id: str = Field(min_length=1, description="从 list_creator_series 取得的真实栏目 ID。")
    state: Literal["all", "pending", "queued"] = Field(default="all", description="all 全部；pending 调研待选；queued 已确认入队。待选不可直接生产。")


class StartRunArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    topic_id: str | None = Field(default=None, min_length=1, max_length=80,
        description="单个真实选题 ID；与 topic_ids 二选一。")
    topic_ids: list[str] | None = Field(default=None, min_length=1, max_length=100,
        description="用户明确要求批量生产的真实选题 ID 列表；后台串行执行，不批准或发布。")
    mode: Literal["start", "fresh"] = Field(default="start",
        description="start 普通提交；用户明确要求清空旧上下文、按最新 Skill 从头重做时用 fresh，仅支持单选题。")
    source_run_id: str | None = Field(default=None, min_length=1, max_length=80,
        description="fresh 模式的原 Run ID，先查 get_content_run 确认。")
    expected_version: int | None = Field(default=None, gt=0, description="fresh 模式所见原 Run version。")
    series_id: str | None = Field(default=None, min_length=1, description="按用户给的参考作品直接制作时的目标栏目；不要先另调候选调研。")
    title: str | None = Field(default=None, min_length=1, max_length=240, description="直接制作的简短选题名称，仅用于展示，不替代原始要求。")
    instructions: str = Field(default="", max_length=3000, description="用户对参考作品的完整原始要求；不要缩成标题后丢弃。")
    attachment_ids: list[str] = Field(default_factory=list, max_length=20, description="当前会话参考图片的已有ID，直接制作必须提供。")
    reference_presentation: bool = Field(default=False, description="默认只借鉴内容，用户明确要求同时参考呈现才为true。")

    @model_validator(mode="after")
    def exactly_one(self):
        if self.attachment_ids:
            if not self.series_id or not self.title or self.topic_id or self.topic_ids or self.mode != "start" or self.source_run_id or self.expected_version:
                raise ValueError("参考作品直接制作需要series_id、title和attachment_ids，不能混用已有选题/重做参数。")
            return self
        if self.series_id or self.title or self.instructions or self.reference_presentation:
            raise ValueError("参考制作参数必须与当前会话图片一起提供。")
        if (self.topic_id is None) == (self.topic_ids is None):
            raise ValueError("topic_id 与 topic_ids 必须二选一。")
        if self.topic_ids and any(not value.strip() or len(value) > 80 for value in self.topic_ids):
            raise ValueError("请选择有效选题。")
        if self.mode == "fresh" and (self.topic_id is None or self.source_run_id is None or self.expected_version is None):
            raise ValueError("从头重做仅支持单选题，且需要来源 Run 和当前版本。")
        if self.mode == "start" and (self.source_run_id is not None or self.expected_version is not None):
            raise ValueError("普通生产不接受从头重做参数。")
        return self


class GetRunArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    run_id: str = Field(min_length=1, description="生产工具或目录返回的 Run ID。")


class InstallSkillArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    github_url: str = Field(description="用户明确要求安装的 GitHub 仓库或 tree/ref/skill-path 链接。")
    retry: bool = Field(default=False, description="仅用户明确要求重试失败/中断的安装时为 true；可能再次消耗额度。")
    role: Literal["mind", "production", "legacy_end_to_end"] | None = Field(
        default=None, description="Skill 角色：mind 内容方法；production 制作呈现；省略表示暂不分类（可展示不可生产）。")


class ComposeSeriesArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120, description="栏目名称。")
    description: str = Field(default="", max_length=10_000, description="栏目定位。")
    audience: str = Field(default="", max_length=4_000, description="目标受众。")
    creator_id: str | None = Field(default=None, description="归属账号 ID；省略则暂不分配，生产前必须分配。")
    skill_name: str | None = Field(default=None, description="Skill 目录返回的完整制作 Skill ID；与双 Skill 组合二选一。")
    mind_skill_id: str | None = Field(default=None, description="Skill 目录返回的内容 Skill ID；必须与 production_skill_id 同时提供。")
    production_skill_id: str | None = Field(default=None, description="Skill 目录返回的呈现 Skill ID；必须与 mind_skill_id 同时提供。")


class UpdateCompositionArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    series_id: str = Field(min_length=1, description="目标栏目 ID。")
    mind_skill_id: str = Field(min_length=1, description="list_producer_skills 返回的内容 Skill ID。")
    production_skill_id: str = Field(min_length=1, description="list_producer_skills 返回的呈现 Skill ID。")
    expected_revision: int = Field(ge=1, description="当前栏目的 revision；先从 list_creator_series 查询取得，过期会被拒绝。")


class AssignSeriesArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    series_id: str = Field(min_length=1, description="目标栏目 ID。")
    creator_id: str | None = Field(description="归属账号 ID；null 表示撤回分配。撤回后该栏目不可生产。")
    expected_revision: int = Field(ge=1, description="当前栏目的 revision；先查询取得，过期会被拒绝。")


class QueueTopicItem(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=240, description="选题标题。")
    brief: str | None = Field(default=None, max_length=4_000, description="切入点/简介；保留候选来源信息。")
    source: Literal["research", "manual"] = Field(default="manual", description="调研候选为 research，手动添加为 manual。")
    reference_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$", description="候选返回的reference_id原样保留，使生产继续读取原图和要求；没有就省略，不能猜测。")


class QueueTopicsArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    series_id: str = Field(min_length=1, description="目标栏目 ID。")
    topics: list[QueueTopicItem] = Field(min_length=1, max_length=50, description="按用户指定的条目标题/切入点直接入队。")
    summary: str | None = Field(default=None, max_length=500, description="本次入队的用户原话摘要，用于审计。")


def compose_series(name, description="", audience="", creator_id=None, skill_name=None,
                   mind_skill_id=None, production_skill_id=None, context=None):
    payload = {"name": name, "description": description, "audience": audience, "creator_id": creator_id,
               "skill_name": skill_name, "mind_skill_id": mind_skill_id, "production_skill_id": production_skill_id,
               "request_id": uuid4().hex}
    return _call(lambda c: _series_receipt(c.request("POST", "/api/series", payload=payload)), context)


def update_series_composition(series_id, mind_skill_id, production_skill_id, expected_revision, context=None):
    payload = {"mind_skill_id": mind_skill_id, "production_skill_id": production_skill_id,
               "expected_revision": expected_revision, "request_id": uuid4().hex}
    return _call(lambda c: _series_receipt(c.request("POST", f"/api/series/{quote(series_id, safe='')}/composition", payload=payload)), context)


def assign_series(series_id, creator_id, expected_revision, context=None):
    payload = {"creator_id": creator_id, "expected_revision": expected_revision, "request_id": uuid4().hex}
    return _call(lambda c: _series_receipt(c.request("POST", f"/api/series/{quote(series_id, safe='')}/assignment", payload=payload)), context)


def _series_receipt(data):
    """Navigation follows the guarded HTTP result, never a model-supplied ID."""
    series = data.get("series") if isinstance(data, dict) else None
    identity = series.get("id") if isinstance(series, dict) else None
    if not isinstance(identity, str) or not identity:
        return data
    return {**data, "series_id": identity, "url": f"/series/{quote(identity, safe='')}"}


def queue_topics(series_id, topics, summary=None, context=None):
    items = [QueueTopicItem.model_validate(t).model_dump() for t in topics]
    for item in items:
        if item.get("reference_id") is None:
            item.pop("reference_id", None)
    request_id, marker = _queue_identity(series_id, items, context)
    payload = {"topics": items, "summary": summary, "request_id": request_id}
    def submit(client):
        path = f"/api/series/{quote(series_id, safe='')}/queue"
        if marker is not None:
            marker.parent.mkdir(parents=True, exist_ok=True)
            try:
                # Reserve before POST. A crash/unknown result becomes a read, never a retry.
                with marker.open("x", encoding="utf-8") as stream:
                    json.dump({"request_id": request_id, "series_id": series_id}, stream)
            except FileExistsError:
                data = client.request("GET", f"{path}/receipts/{request_id}")
                if data.get("status") == "unknown":
                    return ToolResult(content=json.dumps({**data, "url": f"/series/{series_id}",
                        "message": "尚未查到该次入队的成功回执，结果仍不确定；本次未重新提交。"}, ensure_ascii=False),
                        is_error=True, error_type="studio_outcome_unknown")
                return {**data, "series_id": series_id, "url": f"/series/{series_id}"}
        try:
            data = client.request("POST", path, payload=payload)
        except StudioClientError as error:
            error.details.update(request_id=request_id, series_id=series_id,
                                 receipt_url=f"{path}/receipts/{request_id}")
            raise
        return {**data, "series_id": series_id, "url": f"/series/{series_id}",
                "message": "已入队。"}
    return _call(submit, context)


def _queue_identity(series_id, topics, context):
    """Same session + user turn + semantic action; summary is audit-only."""
    turn_id = getattr(context, "user_request_id", None)
    session_file = getattr(context, "session_file", None)
    if not turn_id or session_file is None:
        # Direct/internal calls without a host turn retain the existing new-write behavior.
        return uuid4().hex, None
    identity = {"version": 1, "tool": "queue_topics", "turn": turn_id,
                "session": getattr(context, "agent_session_id", None) or str(session_file.resolve()),
                "creator": getattr(context, "creator_id", None), "series": series_id, "topics": topics}
    request_id = sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False,
                                  separators=(",", ":")).encode("utf-8")).hexdigest()
    return request_id, session_file.with_suffix(".actions") / f"{request_id}.json"


class SkillJobArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    job_id: str = Field(pattern=r"^[a-f0-9]{64}$", description="安装工具返回的真实任务 ID。")


class ProducerSkillArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    skill_id: str = Field(pattern=r"^(?:knowledge-to-carousel|[a-z0-9-]+--[a-f0-9]{16})$",
                          description="从 list_producer_skills 或栏目组合元数据取得的 Skill ID。")
    offset: int = Field(default=0, ge=0, description="正文字符分页起点；有更多内容时按 page.has_more 继续。")
    limit: int = Field(default=2000, ge=1, le=4000, description="本页最多字符数，最大 4000。")
    path: str | None = Field(default=None, max_length=512,
                             description="只读取指定 Markdown/UTF-8 文本；路径须先从 list_files=true 的结果取得。"
                                         "图片路径返回能力错误，文件列表不代表实际看图。")
    list_files: bool = Field(default=False, description="true 时列出受管工作副本文件；不读取正文。")


class UpdateProducerSkillFileArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    skill_id: str = Field(pattern=r"^[a-z0-9-]+--[a-f0-9]{16}$",
                          description="从 list_producer_skills 取得的已安装 Skill ID。")
    path: str = Field(min_length=1, max_length=512,
        description="文字/替换/删除使用文件列表中的相对路径；新增图片使用 assets/ 下的新相对文件名。")
    content: str | None = Field(default=None, max_length=512 * 1024,
                               description="write_text 时提供完整的新 UTF-8 文本；图片操作不传此字段。")
    operation: Literal["write_text", "add_image", "replace_image", "delete_image"] = Field(
        default="write_text", description="默认改文字；新增/替换/删除 assets 内的参考图分别用 add_image/replace_image/delete_image。")
    attachment_id: str | None = Field(default=None, min_length=1, max_length=128,
        description="新增或替换图片时提供当前会话真实附件ID，不传任意图片路径或base64。")
    expected_digest: str = Field(pattern=r"^[a-f0-9]{64}$",
                                 description="get_producer_skill(list_files=true) 或 path 读取返回的当前工作副本 digest。"
                                             "分页正文不提供该值；禁止猜测或使用 source_digest。")

    @model_validator(mode="after")
    def matching_content(self):
        if self.operation == "write_text":
            if self.content is None or self.attachment_id is not None:
                raise ValueError("改文字需要完整 content，不能同时传图片附件。")
        elif self.content is not None:
            raise ValueError("图片操作不接受文本 content。")
        elif self.operation in {"add_image", "replace_image"}:
            if not self.attachment_id:
                raise ValueError("新增或替换参考图需要当前会话 attachment_id。")
        elif self.attachment_id is not None:
            raise ValueError("删除参考图不需要上传附件。")
        return self


class NoArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


def install_producer_skill(github_url, retry=False, role=None, context=None):
    return _call(lambda c: c.request("POST", "/api/producer-skills/install",
                                     payload={"github_url": github_url, "retry": retry, "role": role}), context)


def get_skill_install(job_id, context=None):
    return _call(lambda c: c.request("GET", f"/api/producer-skills/jobs/{job_id}"), context)


def list_producer_skills(context=None):
    return _call(lambda c: c.request("GET", "/api/producer-skills"), context)


def get_producer_skill(skill_id, offset=0, limit=2000, path=None, list_files=False, context=None):
    if list_files:
        return _call(lambda c: c.request(
            "GET", f"/api/producer-skills/{quote(skill_id, safe='')}/files"), context)
    if path is not None:
        return _call(lambda c: c.request(
            "GET", f"/api/producer-skills/{quote(skill_id, safe='')}/files/text",
            params={"path": path}), context)
    return _call(lambda c: c.request("GET", f"/api/producer-skills/{quote(skill_id, safe='')}/content",
                                     params={"offset": offset, "limit": limit}), context)


def update_producer_skill_file(skill_id, path, content=None, expected_digest=None, context=None,
                               operation="write_text", attachment_id=None):
    values = UpdateProducerSkillFileArgs(skill_id=skill_id, path=path, content=content,
        expected_digest=expected_digest, operation=operation, attachment_id=attachment_id)
    if operation != "write_text":
        from .chat_images import _action
        return _action("skill-asset", values.model_dump(exclude_none=True), context)
    return _call(lambda c: c.request(
        "PUT", f"/api/producer-skills/{quote(skill_id, safe='')}/files/content",
        payload={"path": path, "content": content, "expected_digest": expected_digest}), context)


def _call(action, context=None):
    url = getattr(context, "studio_url", None)
    agent_session_id = getattr(context, "agent_session_id", None)
    client = (StudioClient(url, agent_session_id=agent_session_id) if url
              else StudioClient.from_defaults(agent_session_id=agent_session_id))
    try:
        data = action(client)
        if isinstance(data, ToolResult):
            return data
        return ToolResult(content=json.dumps(data, ensure_ascii=False))
    except StudioClientError as error:
        return ToolResult(
            content=json.dumps({"error": error.code, "message": str(error), "run_id": error.run_id,
                                "url": f"{client.base_url}/runs/{error.run_id}" if error.run_id else None,
                                **error.details},
                               ensure_ascii=False),
            is_error=True, error_type=error.code,
        )
    finally:
        client.close()


def list_creators(offset=0, limit=20, context=None):
    def query(client):
        page = client.creators(offset, limit)
        # Column details are fetched on demand, not repeated for every creator.
        page["items"] = [{k: v for k, v in item.items() if k != "series"} for item in page["items"]]
        return page
    return _call(query, context)


def list_creator_series(creator_id, context=None):
    return _call(lambda client: client.creator_series(creator_id), context)


def list_series_topics(series_id, offset=0, limit=20, context=None, state="all"):
    def query(client):
        data = client.request("GET", f"/api/series/{quote(series_id, safe='')}/topic-library",
                              params={"offset": offset, "limit": limit, "state": state})
        return {**data, "series_id": series_id, "url": f"/series/{quote(series_id, safe='')}"}
    return _call(query, context)


def _prepare_task_reference(client, context, series_id, attachment_ids, instructions, reference_presentation):
    if not attachment_ids:
        return None
    sid = getattr(context, "agent_session_id", None)
    if not sid:
        raise StudioClientError("图片参考需要当前Studio对话。", code="session_required")
    ref = client.request("POST", f"/api/agent/sessions/{quote(sid, safe='')}/image-actions/reference", payload={
        "series_id": series_id, "attachment_ids": attachment_ids, "instructions": instructions,
        "reference_presentation": reference_presentation, "origin_turn_id": getattr(context, "user_request_id", None)})
    return ref["id"]


def start_content_run(topic_id=None, context=None, topic_ids=None, mode="start", source_run_id=None, expected_version=None,
                      series_id=None, title=None, instructions="", attachment_ids=None, reference_presentation=False):
    if attachment_ids:
        def direct(client):
            ref = _prepare_task_reference(client, context, series_id, attachment_ids, instructions, reference_presentation)
            # The same reference brief in this conversation is the same request,
            # including a later turn asking about an unknown response. Deliberate
            # regeneration uses the explicit fresh-Run path, not a new hidden key.
            identity = ["reference-production-v1", getattr(context, "agent_session_id", None), series_id, ref, title]
            request_id = sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
            try:
                result = client.request("POST", f"/api/series/{quote(series_id, safe='')}/reference-production",
                    payload={"reference_id": ref, "title": title, "request_id": request_id})
            except StudioClientError as error:
                error.details.update(request_id=request_id, series_id=series_id,
                    topic_id="reference-" + sha256(json.dumps([series_id, request_id]).encode()).hexdigest()[:24],
                    receipt_url=f"/api/series/{series_id}/queue/receipts/{request_id}", url=f"/series/{series_id}")
                raise
            queue = result.get("production_queue") or {}
            return client.run_summary(result, accepted=queue.get("status") in {"pending", "running"})
        return _call(direct, context)
    if topic_ids is not None:
        return _call(lambda client: client.start_batch(topic_ids), context)
    if mode == "fresh":
        # Host-owned identity is stable across repeated tool calls in one user turn.
        # A new user turn is a new request, still guarded by the source version/latest Run.
        turn = getattr(context, "user_request_id", None)
        session = getattr(context, "agent_session_id", None) or str(getattr(context, "session_file", ""))
        identity = json.dumps(["fresh-production-v1", session, turn, source_run_id], ensure_ascii=False)
        request_id = sha256(identity.encode()).hexdigest() if turn else uuid4().hex
        return _call(lambda client: client.start(topic_id, mode=mode, source_run_id=source_run_id,
            expected_version=expected_version, request_id=request_id), context)
    return _call(lambda client: client.start(topic_id), context)


def get_content_run(run_id, context=None):
    return _call(lambda client: client.run_summary(client.get_run(run_id)), context)
