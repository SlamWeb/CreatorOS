"""Thin model-facing adapters over the same Studio API used by the browser."""
import json

from pydantic import BaseModel, ConfigDict, Field

from ..integrations.studio import StudioClient, StudioClientError
from .results import ToolResult


class PageArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    offset: int = Field(default=0, ge=0, description="分页起点；还有数据时继续翻页。")
    limit: int = Field(default=20, ge=1, le=100, description="每页数量，最多 100。")


class CreatorArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    creator_id: str = Field(min_length=1, description="从 list_creators 取得的真实账号 ID。")


class TopicsArgs(PageArgs):
    series_id: str = Field(min_length=1, description="从 list_creator_series 取得的真实栏目 ID。")


class StartRunArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    topic_id: str = Field(min_length=1, description="从 list_series_topics 取得的真实选题 ID；用户明确要求生产后才提交。")


class GetRunArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    run_id: str = Field(min_length=1, description="生产工具或目录返回的 Run ID。")


def _call(action):
    client = StudioClient.from_defaults()
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
    return _call(query)


def list_creator_series(creator_id, context=None):
    return _call(lambda client: client.creator_series(creator_id))


def list_series_topics(series_id, offset=0, limit=20, context=None):
    return _call(lambda client: client.topics(series_id, offset, limit))


def start_content_run(topic_id, context=None):
    return _call(lambda client: client.start(topic_id))


def get_content_run(run_id, context=None):
    return _call(lambda client: client.run_summary(client.get_run(run_id)))
