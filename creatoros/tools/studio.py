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
from ..runs.models import SkillOverrides
from .skill_edit_batch import SkillEditChangeArgs, apply_skill_edit_batch


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


class TaskSkillArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    skill_id: str = Field(pattern=r"^(knowledge-to-carousel|[a-z0-9-]+--[a-f0-9]{16})$")
    digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$",
        description="通常省略，由宿主读取此精确ID的当前摘要；显式提供时按真实内容校验。")
    role: Literal["primary", "supporting"]


class TaskInputsArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    collection_id: str = Field(min_length=1, max_length=128)
    export_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    bgm_material_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$", description="已有音频或视频文件的素材 ID。")
    bgm_reference: str | None = Field(default=None, min_length=1, max_length=10000,
        description="用户给的 BGM 分享原文或链接，原样交给 Codex，不需要预解析。")

    @model_validator(mode="after")
    def one_bgm_source(self):
        if bool(self.bgm_material_id) == bool(self.bgm_reference and self.bgm_reference.strip()):
            raise ValueError("请选择 BGM 素材文件或提供分享文本，两者选一。")
        return self


class ProductionTaskArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=240, description="同一选题内该制作任务的简短名称。")
    brief: str = Field(min_length=1, max_length=10_000, description="本项独立画面的具体文案/场景和制作要求；理由图默认只交付这一张，25张独立理由图应填25项tasks，不在此写‘做25张’。")
    kind: Literal["image", "video"] = "image"
    skill_ids: list[str] | None = Field(default=None, min_length=1, max_length=12,
        description="本栏目中本次使用的 Skill 精确版本 ID，可选多项；宿主冻结各版本。")
    skills: list[TaskSkillArgs] | None = Field(default=None, min_length=1, max_length=12,
        description="本次精确版本ID与角色，恰有一个primary；其余supporting。digest通常省略由宿主读取。")
    inputs: TaskInputsArgs | None = Field(default=None,
        description="视频图片集合/已导出ZIP，以及 BGM 素材ID或分享原文；分享链接不必提前解析。")
    depends_on: list[str] = Field(default_factory=list, max_length=100,
        description="用户明确选择的本选题生图任务ID；等待由后台推进，不轮询。")
    skill_overrides: SkillOverrides | None = Field(default=None,
        description="本任务选择的完整Skill或Mind/Visualize版本及真实expected_digests；省略使用提交时栏目默认。")

    @model_validator(mode="after")
    def typed_selection(self):
        if self.skill_ids is not None:
            if self.skills is not None or self.skill_overrides is not None:
                raise ValueError("请只使用一种 Skill 选择方式。")
            if any(not item.strip() or len(item) > 120 for item in self.skill_ids) or len(set(self.skill_ids)) != len(self.skill_ids):
                raise ValueError("Skill 选择不能为空或重复。")
        if self.skills is not None:
            if self.skill_overrides is not None:
                raise ValueError("skills 不能与旧 skill_overrides 混用。")
            if sum(item.role == "primary" for item in self.skills) != 1:
                raise ValueError("每个任务必须且只能选择一个主 Skill。")
            if len({item.skill_id for item in self.skills}) != len(self.skills):
                raise ValueError("同一任务不能重复引用 Skill。")
        if self.kind == "video" and (not (self.skills or self.skill_ids) or not self.inputs):
            raise ValueError("视频任务需要选择 Skill、素材集，以及 BGM 文件或分享文本。")
        if self.kind == "image" and (self.inputs is not None or self.depends_on):
            raise ValueError("图片任务不接受视频输入或等待依赖。")
        if len(set(self.depends_on)) != len(self.depends_on) or any(not value.strip() or len(value) > 80 for value in self.depends_on):
            raise ValueError("depends_on 必须为不重复的真实任务 ID。")
        return self


class StartRunArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    topic_id: str | None = Field(default=None, min_length=1, max_length=80,
        description="单个真实选题 ID；与 topic_ids 二选一。")
    topic_ids: list[str] | None = Field(default=None, min_length=1, max_length=100,
        description="用户明确要求批量生产的真实选题 ID 列表；后台排队、最多30个选题并行，不批准或发布。")
    mode: Literal["start", "fresh"] = Field(default="start",
        description="start 普通提交；用户明确要求清空旧上下文、按最新 Skill 从头重做时用 fresh，仅支持单选题。")
    source_run_id: str | None = Field(default=None, min_length=1, max_length=80,
        description="fresh 模式的原 Run ID，先查 get_content_run 确认。")
    expected_version: int | None = Field(default=None, gt=0, description="fresh 模式所见原 Run version。")
    series_id: str | None = Field(default=None, min_length=1, description="按用户给的参考作品直接制作时的目标栏目；不要先另调候选调研。")
    title: str | None = Field(default=None, min_length=1, max_length=240, description="直接制作的简短选题名称，仅用于展示，不替代原始要求。")
    instructions: str = Field(default="", max_length=3000, description="参考制作的完整要求；使用 tasks 时作为每个制作任务的共同要求。")
    attachment_ids: list[str] = Field(default_factory=list, max_length=20, description="当前会话参考图片的已有ID，直接制作必须提供。")
    reference_presentation: bool = Field(default=False, description="默认只借鉴内容，用户明确要求同时参考呈现才为true。")
    skill_overrides: SkillOverrides | None = Field(default=None,
        description="仅本次单选题选择完整 Skill 或内容/呈现版本，允许候选；每个指定ID须提供读取时expected_digests。可只换双Skill一边，不改栏目默认。")
    tasks: list[ProductionTaskArgs] | None = Field(default=None, min_length=1, max_length=100,
        description="同一选题的独立制作单位，一项对应一个Codex thread。独立理由图/场景图默认一张一项；25张=25项并行，先写好各自内容。返回同一collection_id；剪辑由计划等待图片完成再执行，分享文本直接放inputs.bgm_reference，无需先解析。不能混用其他生产模式。")

    @model_validator(mode="after")
    def exactly_one(self):
        if self.tasks is not None:
            if (not self.topic_id or self.topic_ids is not None or self.mode != "start"
                    or self.source_run_id is not None or self.expected_version is not None
                    or self.series_id is not None or self.title is not None
                    or self.attachment_ids or self.reference_presentation or self.skill_overrides is not None):
                raise ValueError("制作任务仅接受一个topic_id和tasks，各任务内指定Skill；不能混用其他生产模式。")
            if self.instructions.strip() and any(len(task.brief) + len(self.instructions.strip()) + len('共同要求：\n\n\n本任务：\n') > 10_000 for task in self.tasks):
                raise ValueError("共同要求与单项制作要求合并后不能超过 10000 字。")
            return self
        if self.attachment_ids:
            if self.skill_overrides is not None:
                raise ValueError("指定版本目前使用已有单选题；参考图直接制作不能混用版本覆盖。")
            if not self.series_id or not self.title or self.topic_id or self.topic_ids or self.mode != "start" or self.source_run_id or self.expected_version:
                raise ValueError("参考作品直接制作需要series_id、title和attachment_ids，不能混用已有选题/重做参数。")
            return self
        if self.series_id or self.title or self.instructions or self.reference_presentation:
            raise ValueError("参考制作参数必须与当前会话图片一起提供。")
        if (self.topic_id is None) == (self.topic_ids is None):
            raise ValueError("topic_id 与 topic_ids 必须二选一。")
        if self.topic_ids and any(not value.strip() or len(value) > 80 for value in self.topic_ids):
            raise ValueError("请选择有效选题。")
        if self.topic_ids and self.skill_overrides is not None:
            raise ValueError("指定版本仅支持单选题，不混用批量生产。")
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
    mind_skill_id: str | None = Field(default=None, description="直接引用的原始 Mind Skill ID；与 production_skill_id 同时提供，无需先融合。")
    production_skill_id: str | None = Field(default=None, description="直接参考的原始 Visualize Skill ID；与 mind_skill_id 同时提供，由同一生产会话使用。")


class UpdateCompositionArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)
    series_id: str = Field(min_length=1, description="目标栏目 ID。")
    name: str | None = Field(default=None, min_length=1, max_length=120, description="仅改栏目名称时填写新名称，省略Skill字段；不改ID、选题或产物。")
    mind_skill_id: str | None = Field(default=None, min_length=1, description="更换组合时填写内容 Skill ID，与production_skill_id同时提供。")
    production_skill_id: str | None = Field(default=None, min_length=1, description="更换组合时填写呈现 Skill ID，与mind_skill_id同时提供。")
    expected_revision: int = Field(ge=1, description="当前栏目的 revision；先从 list_creator_series 查询取得，过期会被拒绝。")

    @model_validator(mode="after")
    def edit_one_property_group(self):
        if self.name is not None:
            if self.mind_skill_id is not None or self.production_skill_id is not None:
                raise ValueError("改名只传name，不同时更换Skill组合。")
        elif self.mind_skill_id is None or self.production_skill_id is None:
            raise ValueError("请提供新名称，或完整Mind＋Visualize组合。")
        return self


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


def update_series_composition(series_id, mind_skill_id=None, production_skill_id=None, expected_revision=None, context=None, name=None):
    if name is not None:
        sid, turn = getattr(context, "agent_session_id", None), getattr(context, "user_request_id", None)
        identity = json.dumps([sid, turn, series_id, name, expected_revision], ensure_ascii=False)
        request_id = sha256(identity.encode()).hexdigest() if sid and turn else uuid4().hex
        payload = {"name": name, "expected_revision": expected_revision, "request_id": request_id}
        return _call(lambda c: _series_receipt(c.request("PATCH", f"/api/series/{quote(series_id, safe='')}", payload=payload)), context)
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
                             description="只读取指定 Markdown/UTF-8 文本；已知根 SKILL.md 可直接读取，无需先列文件。"
                                         "可与 list_files=true 一次返回文件树和该正文。图片路径仍不返回图像字节。")
    list_files: bool = Field(default=False, description="true 时列出受管工作副本文件；与 path 同传可同时读取指定正文。")
    include_versions: bool = Field(default=False, description="true 时只读取该 Skill 家族历史版本、候选和 digest；与 path/list_files 互斥。")

    @model_validator(mode="after")
    def exclusive_version_listing(self):
        if self.include_versions and (self.path is not None or self.list_files):
            raise ValueError("版本列表不能与正文或文件树读取组合。")
        return self


class ListProducerSkillsArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    include_versions: bool = Field(default=False, description="包含当前可访问Skill的历史版本和候选。")
    family_skill_id: str | None = Field(default=None,
        pattern=r"^[a-z0-9-]+--[a-f0-9]{16}$", description="只查看这个真实Skill所属家族，可找到刚创建的候选。")


class UpdateProducerSkillFileArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    skill_id: str = Field(pattern=r"^[a-z0-9-]+--[a-f0-9]{16}$",
                          description="从 list_producer_skills 取得的已安装 Skill ID。")
    path: str | None = Field(default=None, min_length=1, max_length=512,
        description="单项文字/图片操作的相对路径；批量编辑时每个 changes 项各自提供 path。")
    content: str | None = Field(default=None, max_length=512 * 1024,
                               description="write_text 时提供完整的新 UTF-8 文本；图片操作不传此字段。")
    operation: Literal["write_text", "add_image", "replace_image", "delete_image", "batch"] = Field(
        default="write_text", description="默认单项改文字；图片使用 add_image/replace_image/delete_image；多项使用 batch。")
    attachment_id: str | None = Field(default=None, min_length=1, max_length=128,
        description="新增或替换图片时提供当前会话真实附件ID，不传任意图片路径或base64。")
    expected_digest: str = Field(pattern=r"^[a-f0-9]{64}$",
                                 description="get_producer_skill(list_files=true) 或 path 读取返回的当前工作副本 digest。"
                                             "分页正文不提供该值；禁止猜测或使用 source_digest。")
    changes: list[SkillEditChangeArgs] | None = Field(default=None, min_length=1, max_length=20,
        description="operation=batch 时按顺序执行 1–20 项；使用每项真实回执更新候选 ID 与 digest，失败即停，不回滚。")

    @model_validator(mode="after")
    def matching_content(self):
        if self.operation == "batch":
            if self.changes is None or self.path is not None or self.content is not None or self.attachment_id is not None:
                raise ValueError("批量编辑需要 changes，不能混用顶层 path/content/attachment_id。")
            return self
        if self.changes is not None or self.path is None:
            raise ValueError("单项编辑需要 path，且不能同时传 changes。")
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


def list_producer_skills(context=None, include_versions=False, family_skill_id=None):
    params = {"include_versions": include_versions}
    if family_skill_id is not None:
        params["family_skill_id"] = family_skill_id
    return _call(lambda c: c.request("GET", "/api/producer-skills", params=params), context)


def get_producer_skill(skill_id, offset=0, limit=2000, path=None, list_files=False, context=None, include_versions=False):
    args = ProducerSkillArgs(skill_id=skill_id, offset=offset, limit=limit, path=path,
                             list_files=list_files, include_versions=include_versions)
    if include_versions:
        return _call(lambda c: c.request("GET", f"/api/producer-skills/{quote(skill_id, safe='')}/versions"), context)
    if list_files and path is not None:
        def query(client):
            files = client.request("GET", f"/api/producer-skills/{quote(skill_id, safe='')}/files")
            text = client.request("GET", f"/api/producer-skills/{quote(skill_id, safe='')}/files/text",
                                  params={"path": path})
            if files.get("digest") != text.get("digest"):
                raise StudioClientError(
                    "Skill 文件树与正文读取期间版本已变化；本次未合并不同版本。",
                    "skill_digest_conflict", details={"current_digest": text.get("digest")},
                )
            return {**files, "path": text["path"], "kind": text["kind"],
                    "content": text["content"]}
        return _call(query, context)
    if list_files:
        return _call(lambda c: c.request(
            "GET", f"/api/producer-skills/{quote(skill_id, safe='')}/files"), context)
    if path is not None:
        return _call(lambda c: c.request(
            "GET", f"/api/producer-skills/{quote(skill_id, safe='')}/files/text",
            params={"path": path}), context)
    return _call(lambda c: c.request("GET", f"/api/producer-skills/{quote(skill_id, safe='')}/content",
                                     params={"offset": offset, "limit": limit}), context)


def update_producer_skill_file(skill_id, path=None, content=None, expected_digest=None, context=None,
                               operation="write_text", attachment_id=None, changes=None):
    values = UpdateProducerSkillFileArgs(skill_id=skill_id, path=path, content=content,
        expected_digest=expected_digest, operation=operation, attachment_id=attachment_id,
        changes=changes)
    if operation == "batch":
        return apply_skill_edit_batch(skill_id, expected_digest, values.changes, context)
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
                      series_id=None, title=None, instructions="", attachment_ids=None, reference_presentation=False,
                      skill_overrides=None, tasks=None):
    if tasks is not None:
        selected = [ProductionTaskArgs.model_validate(task).model_dump(mode="json", exclude_none=True)
                    for task in tasks]
        if instructions.strip():
            for task in selected:
                task["brief"] = f"共同要求：\n{instructions.strip()}\n\n本任务：\n{task['brief']}"
        turn = getattr(context, "user_request_id", None)
        session = getattr(context, "agent_session_id", None) or str(getattr(context, "session_file", ""))
        # One user turn may authorize image tasks and then their dependent video.
        # The trusted turn plus normalized payload identifies one submission;
        # a byte-equivalent retry reuses it, a different stage gets its own key.
        path = f"/api/topics/{quote(topic_id, safe='')}/production-tasks"
        def submit_tasks(client):
            request_id = None
            try:
                tasks_to_submit = json.loads(json.dumps(selected))
                digests = {}
                for task in tasks_to_submit:
                    for skill in task.get("skills", []):
                        skill_id = skill["skill_id"]
                        if skill_id not in digests:
                            digests[skill_id] = client.request("GET", f"/api/producer-skills/{quote(skill_id, safe='')}/files")["digest"]
                        actual = digests[skill_id]
                        if skill.get("digest") is not None and skill["digest"] != actual:
                            raise StudioClientError("所选 Skill 内容已变化，请核对后再提交。", code="skill_digest_conflict",
                                details={"skill_id": skill_id, "current_digest": actual})
                        skill["digest"] = actual
                identity = ["production-tasks-v2", session, turn, getattr(context, "creator_id", None), topic_id, tasks_to_submit]
                request_id = sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False,
                    separators=(",", ":")).encode()).hexdigest() if turn else uuid4().hex
                data = client.request("POST", path, payload={"request_id": request_id, "tasks": tasks_to_submit})
            except StudioClientError as error:
                error.details.update(topic_id=topic_id, receipt_url=path)
                if request_id:
                    error.details["request_id"] = request_id
                raise
            return production_tasks_receipt(data, context)
        return _call(submit_tasks, context)
    if skill_overrides is not None:
        selected = SkillOverrides.model_validate(skill_overrides).model_dump(mode="json", exclude_none=True)
        turn = getattr(context, "user_request_id", None)
        session = getattr(context, "agent_session_id", None) or str(getattr(context, "session_file", ""))
        identity = ["selected-production-v1", session, turn, mode, source_run_id, topic_id]
        if not turn:
            identity.append(selected)
        request_id = sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return _call(lambda client: client.start(topic_id, mode=mode, source_run_id=source_run_id,
            expected_version=expected_version, request_id=request_id, skill_overrides=selected), context)
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


def production_tasks_receipt(data, context=None):
    """Keep original DTO evidence; navigation uses only guarded response identities."""
    result = {**data, "items": [dict(item, **({"url": f"/runs/{quote(item['run_id'], safe='')}"}
              if isinstance(item.get("run_id"), str) and item["run_id"] else {}))
              for item in data.get("items", [])]}
    creator_id = data.get("creator_id") or getattr(context, "creator_id", None)
    if creator_id and data.get("collection_id"):
        result["collection_url"] = (f"/?creator={quote(creator_id, safe='')}&view=materials"
                                    f"&collection={quote(data['collection_id'], safe='')}")
    return result
