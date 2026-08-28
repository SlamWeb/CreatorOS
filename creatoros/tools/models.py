from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReadFileArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    path: str = Field(description="相对于 CreatorOS 项目目录的文件路径。")
    offset: int = Field(default=1, ge=1, description="从第几行开始读取，第一行是 1。")
    limit: int | None = Field(default=None, ge=1, description="最多读取多少行，不填写则读取到文件结尾。")


class WriteFileArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    path: str = Field(description="相对于 CreatorOS 项目目录的新文件路径。")
    content: str = Field(description="要写入文件的完整文本内容。")


class ReadToolResultArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    result_ref: str = Field(
        min_length=1,
        description="被截断工具结果标记中的 result_ref。",
    )
    offset: int = Field(
        default=1,
        ge=1,
        description="从第几个字符开始读取，第一个字符是 1。",
    )
    limit: int = Field(
        default=8_000,
        ge=1,
        le=16_000,
        description="最多读取多少个字符，最大 16000。",
    )


class AddAuthorArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    author: str = Field(description="知乎作者主页 URL、用户名或 PersonClone 支持的作者标识。")
    kinds: list[Literal["answer", "article", "pin"]] = Field(
        default_factory=lambda: ["answer", "article", "pin"],
        description="要抓取的内容类型，默认抓取回答、文章和想法。",
    )
    max_items: int | None = Field(
        default=None,
        ge=1,
        le=10000,
        description="最多抓取多少条内容；不填写表示使用 PersonClone 默认值。",
    )


class GetAuthorJobArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    job_id: str = Field(min_length=1, description="PersonClone 返回的作者入库任务句柄。")


class WaitAuthorJobArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    job_id: str = Field(min_length=1, description="PersonClone 返回的作者入库任务句柄。")
    timeout_seconds: int = Field(
        default=600,
        ge=1,
        le=1_800,
        description="最多在当前请求中等待多少秒，超时只停止等待，不代表远端任务失败。",
    )
    poll_interval_seconds: float = Field(
        default=3.0,
        ge=0.1,
        le=30.0,
        description="查询任务状态的间隔秒数，默认每 3 秒查询一次。",
    )


class RouteHotspotsArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    limit: int = Field(
        default=10,
        ge=1,
        le=30,
        description="读取多少条知乎热榜候选，默认 10，最多 30。",
    )
    top_k: int = Field(
        default=3,
        ge=1,
        le=10,
        description="每位作者保留多少条最匹配的热点，默认 3，最多 10。",
    )


class AskAuthorArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    author: str = Field(description="已完成索引的 PersonClone 作者标识。")
    question: str = Field(description="交给该作者数字分身回答的问题。")
    query_mode: Literal["raw", "grounded"] = Field(
        default="grounded",
        description="grounded 使用作者知识库和查询理解；raw 用于旁路调试。",
    )
    writer_prompt: Literal["current", "strong_identity", "persona_pack", "mrprompt"] = Field(
        default="strong_identity",
        description="PersonClone 写作策略；默认 strong_identity 不依赖 Narrative Schema，确认作者有 Schema 后可用 mrprompt。",
    )
    parent_top_k: int = Field(
        default=20,
        ge=1,
        description="grounded 检索时最多使用多少条父级内容，默认 20。",
    )


class ZhihuHotListArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    limit: int = Field(
        default=10,
        ge=1,
        le=30,
        description="返回多少条知乎热榜候选，默认 10，最多 30。",
    )


class ZhihuSearchArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    query: str = Field(
        min_length=1,
        max_length=100,
        description="要在知乎站内搜索的具体关键词或问题。",
    )
    count: int = Field(
        default=10,
        ge=1,
        le=10,
        description="返回多少条搜索结果，默认 10，最多 10。",
    )
