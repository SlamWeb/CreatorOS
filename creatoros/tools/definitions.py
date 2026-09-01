import json

from .builtins import (
    get_current_date,
    get_current_time,
    read_file,
    read_tool_result,
    write_file,
)
from .models import (
    AddAuthorArgs,
    AskAuthorArgs,
    GetAuthorJobArgs,
    ProduceContentPackArgs,
    ReadFileArgs,
    ReadToolResultArgs,
    RouteAndAnswerArgs,
    RouteHotspotsArgs,
    WriteFileArgs,
    WaitAuthorJobArgs,
    ZhihuHotListArgs,
    ZhihuSearchArgs,
)
from .content import produce_content_pack
from .personclone import add_author, ask_author, get_author_job, list_authors, wait_author_job
from .creator_routing import route_hotspots
from .zhihu import get_zhihu_hot_list, search_zhihu


def _run_route_and_answer(*args, **kwargs):
    # Lazy import keeps the skills package independent from Tool Registry startup.
    from ..skills.route_and_answer.runner import run_route_and_answer

    return run_route_and_answer(*args, **kwargs)


class Tool:
    def __init__(
        self,
        name,
        description,
        execute,
        parameters=None,
        args_model=None,
        expose_to_model=True,
    ):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.execute = execute
        self.args_model = args_model
        self.expose_to_model = expose_to_model

    def to_schema(self):
        parameters = (
            self.args_model.model_json_schema()
            if self.args_model is not None
            else self.parameters
        )
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": parameters,
            },
        }

    def parse_arguments(self, raw_arguments):
        if self.args_model is not None:
            return self.args_model.model_validate_json(raw_arguments or "{}").model_dump()

        arguments = json.loads(raw_arguments or "{}")
        if not isinstance(arguments, dict):
            raise ValueError("工具参数必须是 JSON object。")
        return arguments


tool_registry = {
    tool.name: tool
    for tool in [
        Tool(
            name="get_current_time",
            description="获取当前本地时间。",
            parameters={"type": "object", "properties": {}, "required": []},
            execute=get_current_time,
        ),
        Tool(
            name="get_current_date",
            description="获取当前日期。",
            parameters={"type": "object", "properties": {}, "required": []},
            execute=get_current_date,
        ),
        Tool(
            name="read_file",
            description="读取 CreatorOS 项目目录内不超过 128 KiB 的 UTF-8 文本文件；敏感路径拒绝读取。",
            execute=read_file,
            args_model=ReadFileArgs,
        ),
        Tool(
            name="read_tool_result",
            description="按 result_ref 分段读取 Session 中未截断的历史工具结果文本。",
            execute=read_tool_result,
            args_model=ReadToolResultArgs,
        ),
        Tool(
            name="write_file",
            description="在 CreatorOS 项目目录内创建新的 UTF-8 文本文件，不覆盖已有文件。",
            execute=write_file,
            args_model=WriteFileArgs,
        ),
        Tool(
            name="list_authors",
            description="列出 PersonClone 作者及推荐的回答模式；没有 Narrative Schema 的作者默认使用 strong_identity。",
            parameters={"type": "object", "properties": {}, "required": []},
            execute=list_authors,
        ),
        Tool(
            name="add_author",
            description="请求 PersonClone 抓取并建立一个新的知乎作者数字分身；返回异步任务状态。",
            execute=add_author,
            args_model=AddAuthorArgs,
        ),
        Tool(
            name="get_author_job",
            description="查询 PersonClone 作者入库任务的最新状态和阶段。",
            execute=get_author_job,
            args_model=GetAuthorJobArgs,
        ),
        Tool(
            name="wait_author_job",
            description="在当前请求中轮询 PersonClone 作者入库任务，直到 ready、失败或超时。",
            execute=wait_author_job,
            args_model=WaitAuthorJobArgs,
        ),
        Tool(
            name="produce_content_pack",
            description="把已选知识主题交给 Codex，生成并验收一篇小红书图片轮播；一次调用对应一个可恢复的内容会话。",
            execute=produce_content_pack,
            args_model=ProduceContentPackArgs,
        ),
        Tool(
            name="route_hotspots",
            description="获取知乎热榜并按作者 domain prototype 生成每位作者的 Top-N 热点候选队列。",
            execute=route_hotspots,
            args_model=RouteHotspotsArgs,
        ),
        Tool(
            name="route_and_answer",
            description="根据热点候选选择作者并调用数字分身生成回答；支持预览、确认和自动选择。",
            execute=_run_route_and_answer,
            args_model=RouteAndAnswerArgs,
            expose_to_model=False,
        ),
        Tool(
            name="ask_author",
            description="把问题交给指定的 PersonClone 作者数字分身，并返回它生成的回答。",
            execute=ask_author,
            args_model=AskAuthorArgs,
        ),
        Tool(
            name="get_zhihu_hot_list",
            description="从知乎官方开放平台读取当前结构化热榜，作为选题候选，不负责判断是否值得追。",
            execute=get_zhihu_hot_list,
            args_model=ZhihuHotListArgs,
        ),
        Tool(
            name="search_zhihu",
            description="通过知乎官方开放平台搜索问题、回答和文章，为热点补充社区观点与原文来源。",
            execute=search_zhihu,
            args_model=ZhihuSearchArgs,
        ),
    ]
}


# ``tool_registry`` is the complete execution catalog. ``tools`` is the
# provider-facing schema list and deliberately omits internal-only tools.
tools = [tool.to_schema() for tool in tool_registry.values() if tool.expose_to_model]
