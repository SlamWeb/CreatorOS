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
