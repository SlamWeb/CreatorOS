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
from .studio import (
    PageArgs, CreatorArgs, TopicsArgs, StartRunArgs, GetRunArgs,
    list_creators, list_creator_series, list_series_topics, start_content_run, get_content_run,
    InstallSkillArgs, SkillJobArgs, ProducerSkillArgs,
    UpdateProducerSkillFileArgs, NoArgs, install_producer_skill, get_skill_install,
    list_producer_skills, get_producer_skill, update_producer_skill_file,
    ResearchTopicsArgs, ResearchBatchArgs, SelectResearchArgs,
    research_series_topics, get_topic_research, prepare_topic_selection,
    ComposeSeriesArgs, UpdateCompositionArgs, AssignSeriesArgs, QueueTopicsArgs,
    compose_series, update_series_composition, assign_series, queue_topics,
)
from .skill_extraction import (
    ExtractSkillsFromArtifactArgs, GetSkillExtractionArgs,
    SaveExtractedSkillsArgs, CancelSkillExtractionArgs,
    extract_skills_from_artifact, get_skill_extraction,
    save_extracted_skills, cancel_skill_extraction,
    EditExtractedSkillsArgs, ReviseExtractedSkillsArgs, TrialExtractedSkillsArgs,
    edit_extracted_skills, revise_extracted_skills, trial_extracted_skills,
)


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
            expose_to_model=False,
        ),
        Tool(name="list_creators", description="分页查询 Studio 运营账号（不是 PersonClone 作者）。先查真实目录，不编造 ID。",
             execute=list_creators, args_model=PageArgs),
        Tool(name="research_series_topics", description="用户要求栏目选题调研时委托 Codex 联网研究。Web宿主持续展示同一批次进度并等待结果；CLI可返回后台批次。只产生候选，不入队、不生产。失败或状态未知时先解释原因/查询同一批次，不自动重新提交。",
             execute=research_series_topics, args_model=ResearchTopicsArgs),
        Tool(name="get_topic_research", description="用户询问进展或要求选择时读取调研批次；ready 且非 stale 才可选，queued=true 的候选不可重复入队。",
             execute=get_topic_research, args_model=ResearchBatchArgs),
        Tool(name="prepare_topic_selection", description="把用户选择的候选及标题/切入点修改、顺序生成待确认 Preview，保留来源；不执行入队、不调用生产。歧义先询问。返回链接交用户人工确认。",
             execute=prepare_topic_selection, args_model=SelectResearchArgs),
        Tool(name="queue_topics", description="用户明确指定条目入队时直接写入正式队列（一次完成，含审计）。只用于明确指令：'看看/调研'用别的工具；重复调用返回首次结果不重复写入。",
             execute=queue_topics, args_model=QueueTopicsArgs),
        Tool(name="compose_series", description="用户明确要求创建栏目时使用：legacy 单 Skill 或完整 mind+production 组合二选一；creator_id 可选，未分配栏目生产前必须分配。",
             execute=compose_series, args_model=ComposeSeriesArgs),
        Tool(name="update_series_composition", description="用户明确要求修改栏目 Skill 组合时使用；必须先查询取得当前 revision。旧单 Skill 栏目不可转换，需新建组合栏目。",
             execute=update_series_composition, args_model=UpdateCompositionArgs),
        Tool(name="assign_series", description="用户明确要求时分配或撤回栏目归属账号；creator_id 为 null 表示撤回，撤回后不可生产。必须携带当前 revision。",
             execute=assign_series, args_model=AssignSeriesArgs),
        Tool(name="install_producer_skill", description="用户明确要求安装 GitHub Skill 后，由 CreatorOS 使用 Git 后台下载并核验到本地生产 Skill 库，不调用模型、不安装到 Codex 全局目录。可按用户声明传入 role（mind 内容/production 制作）；省略则暂不分类、不可生产。返回安装任务，不生成内容、不绑定栏目。不要轮询等待；未知结果不得自动重试。",
             execute=install_producer_skill, args_model=InstallSkillArgs),
        Tool(name="get_skill_install", description="用户询问安装进展时查询任务；installed 只代表文件已登记，未绑定栏目，也不保证组合可生产。用户明确要求绑定时，可在栏目页面操作或使用 compose_series/update_series_composition，角色及生产适配器仍需校验。",
             execute=get_skill_install, args_model=SkillJobArgs),
        Tool(name="list_producer_skills", description="列出已登记生产 Skill 的当前本地工作副本、路径、角色与兼容性；不执行安装或生产。",
             execute=list_producer_skills, args_model=NoArgs),
        Tool(name="get_producer_skill", description="按需读取 Skill：默认分页读已登记 Skill 的 SKILL.md；明确要查看/编辑文件时先传 list_files=true 列受管文件，之后传 path 读取其 Markdown/文本正文及 digest。路径只能来自文件列表，不能读任意路径。",
             execute=get_producer_skill, args_model=ProducerSkillArgs),
        Tool(name="update_producer_skill_file", description="仅当用户在当前对话明确授权修改已安装 Skill 时调用；一次原子更新一个已列出的 UTF-8 文本文件，并传入读取时的整个 Skill digest。只改 working 副本；versions 原件与历史 Run 快照不变。Skill 是共享库文件，保存会影响所有绑定该 Skill 的栏目，调用前需向用户说明这一点。SKILL.md 必须保留合法 name/description frontmatter。",
             execute=update_producer_skill_file, args_model=UpdateProducerSkillFileArgs),
        Tool(name="list_creator_series", description="查询指定运营账号下的所有栏目、受众和 Skill；同名栏目需结合账号消歧。",
             execute=list_creator_series, args_model=CreatorArgs),
        Tool(name="list_series_topics", description="统一查询栏目选题库：待选建议与已入队选题，支持 state 筛选。待选使用返回的 batch_id/candidate_id 准备确认，不可直接生产；已入队按 available_actions 操作。序号针对当前列表，歧义先询问。按 page.total 翻页。",
             execute=list_series_topics, args_model=TopicsArgs),
        Tool(name="start_content_run", description="用户明确要求生产时，把真实选题提交 Studio 后台，返回 Run 链接与当前状态；提交不等于完成。不会自动恢复旧任务。busy 或网络结果未知时不要自动重试，也不要轮询等待整篇完成。",
             execute=start_content_run, args_model=StartRunArgs),
        Tool(name="get_content_run", description="用户询问进度时查询同一 Run 的最新状态和链接。awaiting_approval 仅表示待验收，approved 也不代表已发布。",
             execute=get_content_run, args_model=GetRunArgs),
        Tool(name="extract_skills_from_artifact", description="用户明确要求从作品提炼 Skill 时，提交参考图片或原始文案；本地图片限用户明确给出的项目内路径，也可用已有 upload_ids。返回任务句柄供查询，不会自动保存入库、绑定栏目或生产；结果不确定时复用同一 request_id。",
             execute=extract_skills_from_artifact, args_model=ExtractSkillsFromArtifactArgs),
        Tool(name="get_skill_extraction", description="查询提炼任务或列出历史任务；ready 只表示草稿可预览。先展示 Skill 草稿并等待用户明确确认，不能把提炼结果当作已入库。",
             execute=get_skill_extraction, args_model=GetSkillExtractionArgs),
        Tool(name="save_extracted_skills", description="仅在用户查看草稿后明确确认加入 Skill 库时调用；把查询结果中的 digest 原样作为 expected_digest 传入。此操作只登记 Skill，不绑定栏目、不生产、不发布。",
             execute=save_extracted_skills, args_model=SaveExtractedSkillsArgs),
        Tool(name="cancel_skill_extraction", description="按用户要求取消指定提炼任务；不会影响其他提炼或生产任务。",
             execute=cancel_skill_extraction, args_model=CancelSkillExtractionArgs),
        Tool(name="edit_extracted_skills", description="保存用户修改的 Skill 草稿，使用查询得到的 expected_digest；不入正式库。",
             execute=edit_extracted_skills, args_model=EditExtractedSkillsArgs),
        Tool(name="revise_extracted_skills", description="按用户要求让 Codex 修改当前 Skill 草稿；返回任务供查询，不自动试用或入库。",
             execute=revise_extracted_skills, args_model=ReviseExtractedSkillsArgs),
        Tool(name="trial_extracted_skills", description="仅在用户明确要求试生产时，使用当前草稿与用户选题真实生成图片；结果保留草稿区，不创建栏目、不入正式库。",
             execute=trial_extracted_skills, args_model=TrialExtractedSkillsArgs),
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
