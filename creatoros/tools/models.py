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


class AskAuthorArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    author: str = Field(description="已完成索引的 PersonClone 作者标识。")
    question: str = Field(description="交给该作者数字分身回答的问题。")
    query_mode: Literal["raw", "grounded"] = Field(
        default="grounded",
        description="grounded 使用作者知识库和查询理解；raw 用于旁路调试。",
    )
    writer_prompt: Literal["current", "strong_identity", "persona_pack", "mrprompt"] = Field(
        default="mrprompt",
        description="PersonClone 使用的作者写作策略；mrprompt 需要作者已有 Narrative Schema。",
    )
