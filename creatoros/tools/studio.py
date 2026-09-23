"""Thin model-facing adapters over the same Studio API used by the browser."""
import json
from typing import Literal
from urllib.parse import quote
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from ..integrations.studio import StudioClient, StudioClientError
from .results import ToolResult
from ..integrations.topic_research import CandidateSelection


class ResearchTopicsArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    series_id: str = Field(min_length=1, description="真实栏目 ID。使用当前栏目定位、受众和绑定 Skill。")
    count: int = Field(default=10, ge=1, le=30, description="最多候选数；允许调研不足，不凑数。")
    instructions: str = Field(default="", max_length=3000, description="本次选题偏好、排除方向等用户要求。")


class ResearchBatchArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    batch_id: str = Field(pattern=r"^[a-f0-9]{32}$", description="调研返回的真实批次 ID。")


class SelectResearchArgs(ResearchBatchArgs):
    selections: list[CandidateSelection] = Field(min_length=1, max_length=30,
        description="按用户要求的入队顺序列出候选 ID；可改 title/angle，省略则保留原文。只生成 Preview，不确认。")


def research_series_topics(series_id, count=10, instructions="", context=None):
    return _call(lambda c: c.request("POST", f"/api/series/{series_id}/topic-research", payload={"count": count, "instructions": instructions}), context)


def get_topic_research(batch_id, context=None):
    return _call(lambda c: c.request("GET", f"/api/topic-research/{batch_id}"), context)


def prepare_topic_selection(batch_id, selections, context=None):
    items = [s.model_dump() if isinstance(s, CandidateSelection) else s for s in selections]
    def preview(client):
        data = client.request("POST", f"/api/topic-research/{batch_id}/preview", payload={"selections": items})
        return {"operation_id": data["id"], "status": data["status"], "preview": data["preview"],
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
    topic_id: str = Field(min_length=1, description="从 list_series_topics 取得的真实选题 ID；用户明确要求生产后才提交。")


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
    skill_name: str | None = Field(default=None, description="legacy 单 Skill 绑定，如 knowledge-to-carousel；与组合二选一。")
    mind_skill_id: str | None = Field(default=None, description="组合的内容 Skill ID；必须与 production_skill_id 同时提供。")
    production_skill_id: str | None = Field(default=None, description="组合的制作 Skill ID；必须与 mind_skill_id 同时提供。")


class UpdateCompositionArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    series_id: str = Field(min_length=1, description="目标栏目 ID。")
    mind_skill_id: str = Field(min_length=1, description="新的内容 Skill ID。")
    production_skill_id: str = Field(min_length=1, description="新的制作 Skill ID。")
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
    return _call(lambda c: c.request("POST", "/api/series", payload=payload), context)


def update_series_composition(series_id, mind_skill_id, production_skill_id, expected_revision, context=None):
    payload = {"mind_skill_id": mind_skill_id, "production_skill_id": production_skill_id,
               "expected_revision": expected_revision, "request_id": uuid4().hex}
    return _call(lambda c: c.request("POST", f"/api/series/{quote(series_id, safe='')}/composition", payload=payload), context)


def assign_series(series_id, creator_id, expected_revision, context=None):
    payload = {"creator_id": creator_id, "expected_revision": expected_revision, "request_id": uuid4().hex}
    return _call(lambda c: c.request("POST", f"/api/series/{quote(series_id, safe='')}/assignment", payload=payload), context)


def queue_topics(series_id, topics, summary=None, context=None):
    items = [t.model_dump() if isinstance(t, QueueTopicItem) else t for t in topics]
    payload = {"topics": items, "summary": summary, "request_id": uuid4().hex}
    def submit(client):
        data = client.request("POST", f"/api/series/{quote(series_id, safe='')}/queue", payload=payload)
        return {**data, "url": f"/series/{series_id}",
                "message": "已直接入队并记录审计；deduplicated=true 表示该请求已执行过，未重复写入。"}
    return _call(submit, context)


class SkillJobArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    job_id: str = Field(pattern=r"^[a-f0-9]{64}$", description="安装工具返回的真实任务 ID。")


class NoArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


def install_producer_skill(github_url, retry=False, role=None, context=None):
    return _call(lambda c: c.request("POST", "/api/producer-skills/install",
                                     payload={"github_url": github_url, "retry": retry, "role": role}), context)


def get_skill_install(job_id, context=None):
    return _call(lambda c: c.request("GET", f"/api/producer-skills/jobs/{job_id}"), context)


def list_producer_skills(context=None):
    return _call(lambda c: c.request("GET", "/api/producer-skills"), context)


def _call(action, context=None):
    url = getattr(context, "studio_url", None)
    client = StudioClient(url) if url else StudioClient.from_defaults()
    try:
        data = action(client)
        return ToolResult(content=json.dumps(data, ensure_ascii=False))
    except StudioClientError as error:
        return ToolResult(
            content=json.dumps({"error": error.code, "message": str(error), "run_id": error.run_id,
                                "url": f"{client.base_url}/runs/{error.run_id}" if error.run_id else None},
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
    return _call(lambda client: client.request("GET", f"/api/series/{quote(series_id, safe='')}/topic-library",
                 params={"offset": offset, "limit": limit, "state": state}), context)


def start_content_run(topic_id, context=None):
    return _call(lambda client: client.start(topic_id), context)


def get_content_run(run_id, context=None):
    return _call(lambda client: client.run_summary(client.get_run(run_id)), context)
