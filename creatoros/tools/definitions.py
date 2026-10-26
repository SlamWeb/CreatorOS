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
from .publication import PreparePublicationArgs, GetPublicationArgs, prepare_publication, get_publication
from .feedback import QueryFeedbackArgs, query_feedback
from .chat_images import (
    ReadImageArgs, ExtractSkillImagesArgs, ImportContentImagesArgs, CreateSkillDraftArgs,
    read_image, extract_skill_from_images, import_content_images, create_skill_draft,
)
from .personclone import add_author, ask_author, get_author_job, list_authors, wait_author_job
from .creator_routing import route_hotspots
from .zhihu import get_zhihu_hot_list, search_zhihu
from .studio import (
    PageArgs, CreatorArgs, TopicsArgs, StartRunArgs, GetRunArgs,
    list_creators, list_creator_series, list_series_topics, start_content_run, get_content_run,
    InstallSkillArgs, SkillJobArgs, ProducerSkillArgs,
    UpdateProducerSkillFileArgs, NoArgs, ListProducerSkillsArgs, install_producer_skill, get_skill_install,
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
from .content_discussion import (
    CreatorTasksArgs, DiscussContentRunArgs, GetContentDiscussionArgs,
    RequestContentRevisionArgs, discuss_content_run, get_content_discussion,
    get_creator_tasks, request_content_revision,
)
from .skill_iterations import ManageSkillIterationArgs, manage_skill_iteration
from .topics import ManageTopicsArgs, manage_topics
from .media_collections import ManageMediaCollectionArgs, manage_media_collection


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
        Tool(name="manage_media_collection", description="管理当前账号素材集：list可按name筛选文件夹，可include_sources取任务索引；get读取选片/版本或已导出ZIP；create关联选题及额外Run；update用所见expected_version改名/来源/顺序/排除项；remove只删集合不删原图；export把当前保留图片按序保存为ZIP，返回准确下载入口和export_id，供后续剪辑取用，不调用Codex、不生产、不改Skill、不发布。仅导出时，定位collection_id后可直接export，无需先get全部图片；可省略version与snapshot由宿主读取当前选片，指定旧快照会拒绝。未知结果先查询，不换集合或另起生产。",
             execute=manage_media_collection, args_model=ManageMediaCollectionArgs),
        Tool(name="query_feedback", description="只读本地已采集的账号反馈，比较最近 7 天已发布笔记的点赞、收藏等累计指标。支持分页，返回各笔记采样时间和栏目；不刷新平台、不发布、不新增选题。",
             execute=query_feedback, args_model=QueryFeedbackArgs),
        Tool(name="prepare_publication", description="准备已有图文的发布预览，不发布。返回内容页供用户确认；重复请求复用发布单。",
             execute=prepare_publication, args_model=PreparePublicationArgs),
        Tool(name="get_publication", description="查询发布单，或按用户要求核对平台结果/刷新互动数据。不重新发送，不把未知当失败。",
             execute=get_publication, args_model=GetPublicationArgs),
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
        Tool(name="research_series_topics", description="仅在明确需要联网、最新趋势或外部来源核实时委托Codex研究。普通拟题、常识词汇辨析、创意故事和参考图改编默认由你直接完成，不调用本工具。确需联网时可用attachment_ids携带用户参考图与完整instructions。默认只借鉴内容，明确要借鉴呈现才设reference_presentation。只产生候选，不入队或生产；已明确要按参考制作时直接start_content_run。Web宿主展示进度并等待同批次结果；requested_count是请求上限，returned_count是实际候选数，以status判定完成。unknown不等于失败或远端已停止，不自动重提。入队保留reference_id，链接原样使用返回的 url。",
             execute=research_series_topics, args_model=ResearchTopicsArgs),
        Tool(name="get_topic_research", description="用户询问进展或要求选择时读取同一调研批次；ready 且非 stale 才可选，queued=true 的候选不可重复入队。requested_count 是请求上限，returned_count 是本次实际返回的候选数，不代表任务完成；以 status 判定。unknown 不等于失败或远端已停止，不自动重提。链接原样使用返回的 url，不拼接协议、域名或路径。",
             execute=get_topic_research, args_model=ResearchBatchArgs),
        Tool(name="prepare_topic_selection", description="把用户选择的候选及标题/切入点修改、顺序生成待确认 Preview，保留来源；不执行入队、不调用生产。歧义先询问。返回链接交用户人工确认。",
             execute=prepare_topic_selection, args_model=SelectResearchArgs),
        Tool(name="queue_topics", description="用户明确指定栏目和条目时直接入队，不生产。查看/建议/调研不要调用。同一会话同一用户轮次的相同入队动作只写一次，重复只查回执；未知结果不要换参数或另起动作重发。新一轮明确入队属于新指令。",
             execute=queue_topics, args_model=QueueTopicsArgs, expose_to_model=False),
        Tool(name="manage_topics", description="管理选题，不生产：list按栏目筛选待选/已入队并翻页；get读取一条已入队选题和edit_digest；create仅用户明确新增时入队；update用原topic_id、changes和刚读取的edit_digest直接修改标题或完整切入点，不新建替代；remove按用户要求移除指定条目，已有产物保留历史，运行中拒绝。待选remove带真实candidate来源；待选入队用create保留reference_id。management_actions是管理权限，available_actions是生产操作。冲突先重读核对，未知结果不另建条目；不能把修改成功说成图片已更新。",
             execute=manage_topics, args_model=ManageTopicsArgs),
        Tool(name="compose_series", description="用户明确要求创建栏目时使用：一份完整制作 Skill，或直接引用原始 Mind＋Visualize，两者无需先融合、不改写原 Skill。creator_id 可选，未分配栏目生产前必须分配。",
             execute=compose_series, args_model=ComposeSeriesArgs),
        Tool(name="update_series_composition", description="编辑当前栏目：改名仅传name、series_id、expected_revision，不传Skill字段；保留栏目ID、选题与产物，不新建栏目。更换原始Mind＋Visualize时传完整两个Skill ID，不同时改名，无需融合。必须用当前revision，过期重读不能强制覆盖。旧单Skill不可转换组合，但可以改名。",
             execute=update_series_composition, args_model=UpdateCompositionArgs),
        Tool(name="assign_series", description="用户明确要求时分配或撤回栏目归属账号；creator_id 为 null 表示撤回，撤回后不可生产。必须携带当前 revision。",
             execute=assign_series, args_model=AssignSeriesArgs),
        Tool(name="install_producer_skill", description="用户明确要求安装 GitHub Skill 后，由 CreatorOS 使用 Git 后台下载并核验到本地生产 Skill 库，不调用模型、不安装到 Codex 全局目录。可按用户声明传入 role（mind 内容/production 制作）；省略则暂不分类、不可生产。返回安装任务，不生成内容、不绑定栏目。不要轮询等待；未知结果不得自动重试。",
             execute=install_producer_skill, args_model=InstallSkillArgs),
        Tool(name="get_skill_install", description="用户询问安装进展时查询任务；installed 只代表文件已登记，未绑定栏目，也不保证组合可生产。用户明确要求绑定时，可在栏目页面操作或使用 compose_series/update_series_composition，角色及生产适配器仍需校验。",
             execute=get_skill_install, args_model=SkillJobArgs),
        Tool(name="list_producer_skills", description="列出本地Skill发布版元数据；要找修改候选/历史准确版本传include_versions=true，可用family_skill_id限定家族。账号请求仅看到当前账号绑定家族的全部版本。默认目录没有候选不代表候选不存在。正文按需读取，不生产。",
             execute=list_producer_skills, args_model=ListProducerSkillsArgs),
        Tool(name="get_producer_skill", description="读取指定Skill。编辑正文和参考图时一次传path=SKILL.md、list_files=true，取得全文、文件树和同一版本的真实digest；只读已知文件可直接path，无需先列目录。默认分页正文无写入digest。include_versions=true仅在需要定位历史版/候选时查家族，不与文件读取混用；已有准确ID不必再列家族。看图片用read_image。",
             execute=get_producer_skill, args_model=ProducerSkillArgs),
        Tool(name="update_producer_skill_file", description="编辑已有Skill，先读取真实digest。单项write_text改完整文本，add_image/replace_image/delete_image管理assets；多项修改用operation=batch和changes一次提交，按添加图片→改正文引用→删旧图排序，新图用当前会话attachment_id。宿主逐项跟随实际候选ID/digest，失败停止并返回已完成项，不是原子事务；outcome_unknown不能整批重发。发布版另存完整候选，后续用回执新skill_id/digest。只保存候选，不改栏目、旧Run或自动生产；试产用start_content_run指定版本，循环用manage_skill_iteration。",
             execute=update_producer_skill_file, args_model=UpdateProducerSkillFileArgs),
        Tool(name="manage_skill_iteration", description="管理RSI：明确要求循环迭代或本轮开启RSI时start，指定真实栏目、Skill版本与原选题；默认3轮含基线。后台自己提交Codex试产、DS看图评审并改候选，不需另开生产。运行中next_action=wait_background，给任务入口后结束本轮，不连续get；用户后续问进度才get一次。get返回阶段和active_round真实生产状态。pause/resume需当前revision；失败评审或未成功提交时resume沿原图/原输入续接，不加轮数。耗尽后用户明确授权才continue。只交付候选，不发布不切栏目，不混入后来修改的Skill。",
             execute=manage_skill_iteration, args_model=ManageSkillIterationArgs),
        Tool(name="list_creator_series", description="查询指定运营账号下的所有栏目、受众和 Skill；同名栏目需结合账号消歧。",
             execute=list_creator_series, args_model=CreatorArgs),
        Tool(name="list_series_topics", description="统一查询栏目选题库：待选建议与已入队选题，支持 state 筛选。待选使用返回的 batch_id/candidate_id 准备确认，不可直接生产；已入队按 available_actions 操作。序号针对当前列表，歧义先询问。按 page.total 翻页。",
             execute=list_series_topics, args_model=TopicsArgs, expose_to_model=False),
        Tool(name="start_content_run", description="用户明确要求生产时用topic_id单条或topic_ids批量提交。一个选题需多组独立Skill/几十张素材时用topic_id+tasks，每项title/完整brief/可选skill_overrides，后台并行后汇总一个素材集；不复制父Topic，不能混用fresh、批量选题或顶层版本覆盖。后续manage_topics(get)查该选题任务，失败沿原Run恢复/重做，不再创建任务。单条可用skill_overrides指定准确Skill版本（含候选）及expected_digests；先用get_producer_skill(include_versions=true)或文件列表取真实ID/digest，不必切换栏目，只冻结本次选择。批量和参考图入口暂不混用版本覆盖。用户给参考图且已明确制作时直接用series_id/title/完整instructions/attachment_ids，不需先调研；默认只参考内容，可reference_presentation=true。按最新或指定版清空上下文重做用mode=fresh和来源Run/所见version。仅讨论或改Skill不是生产授权。受理不等于完成，不自动恢复旧失败、不批准或发布；未知先查同一任务，不盲目重提。",
             execute=start_content_run, args_model=StartRunArgs),
        Tool(name="get_content_run", description="用户询问进度时查询同一 Run 的最新状态和链接，并返回各版本 revision_id/artifact_digest 以便明确讨论指定版本。awaiting_approval 仅表示待验收，approved 也不代表已发布。",
             execute=get_content_run, args_model=GetRunArgs),
        Tool(name="discuss_content_run", description="用户希望讨论已验收作品时，针对指定 revision 和 artifact_digest 创建只读讨论任务。讨论不修改图片、内容、Skill、审批状态或生产 Run；不自动返工。", execute=discuss_content_run, args_model=DiscussContentRunArgs),
        Tool(name="get_content_discussion", description="读取同一 Run 的讨论历史与任务状态，不调用模型或重复提交。", execute=get_content_discussion, args_model=GetContentDiscussionArgs),
        Tool(name="request_content_revision", description="仅在用户明确要求返工时调用；按当前 Run version 创建待执行 revision，不会执行生产、批准或发布。先查最新 Run 取得 expected_version。", execute=request_content_revision, args_model=RequestContentRevisionArgs),
        Tool(name="get_creator_tasks", description="读取账号现有调研、生产与讨论任务摘要；只投影已有记录，不创建或改变任务状态。", execute=get_creator_tasks, args_model=CreatorTasksArgs),
        Tool(name="read_image", description="实际查看当前会话附件、当前账号 Skill 的参考图或指定产物版本的图片。按返回的附件看图，不猜测图片内容；不接受任意本地路径。", execute=read_image, args_model=ReadImageArgs),
        Tool(name="extract_skill_from_images", description="按用户要求从当前聊天图片提炼Skill草稿。宿主短时等待并展示进度；ready展示草稿，wait_background给任务入口并结束本轮，不连续查询。不会自动入库、组栏目或生产。", execute=extract_skill_from_images, args_model=ExtractSkillImagesArgs),
        Tool(name="create_skill_draft", description="用户已经给出现成提示词或完整Skill，要求保存为可复用Skill时使用。原样保存文字及指定会话参考图，不调用Codex重新提炼；只生成待检查草稿，不入库、不创建栏目、不生产。需要从作品总结方法而没有现成提示词时用extract_skill_from_images。", execute=create_skill_draft, args_model=CreateSkillDraftArgs),
        Tool(name="import_content_images", description="用户明确表示图片已是成品时，按顺序导入指定栏目成为待人工验收内容。不是生图队列，不调用 Codex、不批准、不发布。", execute=import_content_images, args_model=ImportContentImagesArgs),
        Tool(name="extract_skills_from_artifact", description="用户明确要求从作品提炼 Skill 时，提交参考图片或原始文案；本地图片限用户明确给出的项目内路径，也可用已有 upload_ids。返回任务句柄供查询，不会自动保存入库、绑定栏目或生产；结果不确定时复用同一 request_id。",
             execute=extract_skills_from_artifact, args_model=ExtractSkillsFromArtifactArgs),
        Tool(name="get_skill_extraction", description="用户主动询问时查询提炼任务一次，或列出历史任务。运行中给进度和任务入口后结束本轮，禁止连续查询等完成。ready只表示草稿可预览，展示并等待用户确认，不能当作已入库。",
             execute=get_skill_extraction, args_model=GetSkillExtractionArgs),
        Tool(name="save_extracted_skills", description="仅在用户查看草稿后明确确认加入 Skill 库时调用；把查询结果中的 digest 原样作为 expected_digest 传入。此操作只登记 Skill，不绑定栏目、不生产、不发布。",
             execute=save_extracted_skills, args_model=SaveExtractedSkillsArgs),
        Tool(name="cancel_skill_extraction", description="按用户要求取消指定提炼任务；不会影响其他提炼或生产任务。",
             execute=cancel_skill_extraction, args_model=CancelSkillExtractionArgs),
        Tool(name="edit_extracted_skills", description="只编辑尚未入库的ready草稿，使用当前expected_digest。已saved的任务不再编辑草稿：用saved_skills中的准确ID读取已安装Skill，随后用update_producer_skill_file另存候选；不必再查全库或整个家族。不入库、不切栏目。",
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
