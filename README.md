# CreatorOS

面向内容创作者矩阵的自治运营 Agent。CreatorOS 目前也是一个从零构建的 Python Agent Runtime 学习项目：先把模型调用、工具执行、上下文与会话等底层能力做小、做实，再逐步接通创作者运营业务。

> 当前阶段：已打通 Agent Runtime、热点/作者路由与可恢复的 `Topic → Codex → SocialContentPack → 人工批准` 生产链路；质量评审、发布与效果反馈仍在逐步接入。

[源码仓库](https://github.com/SlamWeb/CreatorOS) · [Pi Agent 参考实现](https://github.com/earendil-works/pi) · [Pi 文档](https://pi.dev/docs/latest)

## 产品目标

CreatorOS 的长期目标是把创作者矩阵运营编排成一个可恢复的 Agent：

```text
栏目/热点选题 → Creator/Series Routing → Skill + Content Producer
           → SocialContentPack → 质量评审 → 发布 → 效果反馈
```

运营者最终只需要配置创作者、栏目、日更目标和发布策略；系统负责维护选题列表、内容包与运行状态。PersonClone 实验线继续为数字分身作者维护热点、常青和实验三个候选队列。

## 当前已实现

- **Python Agent Runtime**：Provider 抽象、Agent Loop、Tool Calling、Pydantic 参数校验、结构化 `ToolResult`、MaxTurnGuard。
- **流式终端体验**：DeepSeek SSE 流式响应、Rich Markdown 输出、底部单行工具状态栏、Windows 终端稳定的单向追加渲染。
- **上下文与会话**：`AgentState`、`RuntimeContext`、`ModelContext`、token 预算、自动/手动压缩、`CompactionCheckpoint`、大型 ToolResult 投影和按 `result_ref` 分页读取。
- **知乎数据接入**：官方热榜 API 和站内搜索 API，映射为内部不可变数据模型；只从环境变量读取 Access Secret。
- **PersonClone 接入**：通过独立 FastAPI 服务读取作者列表和 `AuthorRoutingProfile`，复用登录 Cookie；CreatorOS 不读取 PersonClone 本地文件、Qdrant 或原始语料。
- **Skill Loader**：递归发现 `creatoros/skills/**/SKILL.md`，解析名称和描述并注入模型上下文；完整 Skill 正文按需读取，暂不自动执行脚本或持久化激活状态。
- **Creator Routing**：对作者 domain prototypes 与热点标题/介绍使用本地缓存的 BGE-M3 生成向量，并按 `corpus_version` 与文本指纹复用作者原型向量，以作者内部 Max Similarity 完成第一阶段候选召回。
- **作者侧内容队列**：把热点→作者的匹配矩阵反转为每位作者的 Top-N `hot` 队列，并提供作者内 `position` 供交互选择；`evergreen`、`experiment` 队列的数据结构已预留。
- **Agent 可调用路由**：`route_hotspots(limit, top_k)` 将实时热榜、PersonClone 画像、离线 BGE-M3 和作者侧队列编排为一个 Tool；失败或不可用画像会被结构化报告，不读取 PersonClone 本地文件或 Qdrant。
- **route-and-answer Skill**：用 `SKILL.md` 描述“热点匹配→选择作者→生成回答”的流程；普通 Agent 只看到 `read_file`、`route_hotspots`、`ask_author` 等原子工具，Python Runner 作为宿主侧自动化入口保留。
- **图片轮播生产**：`knowledge-to-carousel` 把知识主题约束成原创、零基础友好的小红书图片轮播；`produce_content_pack` 调用已登录 Codex CLI，一篇内容创建一个可恢复 thread，并以 Structured Outputs 返回生产回执。
- **产物契约**：CreatorOS 只接受当前 Codex thread 的真实生成图片，自行复制、写入 Manifest，并用严格 Pydantic `SocialContentPack` 验证卡片顺序、图片路径、发布文案和来源。
- **栏目持久化**：用 SQLAlchemy 建模 `Creator → Series → Topic`，以 Alembic 管理 schema 版本；SQLite 默认本地可用，Repository 支持有序选题和事务化调序。
- **可恢复运营计划**：DeepSeek Responses Structured Output 把自然语言翻译成严格 `OperationPlan`；CLI 展示只读 Preview，支持自由修改、确认和取消，重启后继续处理。业务写入、成功状态与审计事件原子提交，并用状态指纹拒绝过期确认。
- **可恢复内容生产**：`ContentRun → Revision → Attempt` 分离业务生命周期、人工返工与技术重试；Codex thread 在事件流出现时立即持久化，中断后由用户显式恢复，同篇返工复用上下文。
- **产物批准与 Trace**：确定性校验 Manifest、图片路径/顺序/尺寸，并对 canonical Manifest 与有序图片字节计算 digest；批准绑定 Revision 与 digest，append-only Event 保存完整生产 Trajectory。

当前路由结果是候选召回，不是最终发布决策；宽泛领域原型、跨域视角和最终 LLM 重排会在后续切片中单独验证。

## 架构概览

```text
creatoros/
├── ai/            Provider、DeepSeek、ModelContext、流式类型
├── agent/         Agent Loop、State、Guard、Compaction
├── tools/         Tool、Registry、Pydantic Args、ToolResult
├── discovery/     HotTopic、知乎搜索/热榜领域模型
├── integrations/  知乎 OpenAPI、PersonClone、CodexProducer
├── routing/       Profile 模型、投影、BGE-M3、domain 召回
├── planning/      ContentOpportunity、DailyPlan、作者侧队列
├── operations/    OperationPlan、Preview、确认与事务执行
├── runs/          ContentRun 状态机、Revision/Attempt、验收与批准
├── storage/       SQLAlchemy 模型、Database、Repository、Alembic 入口
├── skills/        Skill Loader、SKILL.md、业务 Skill Runner
├── session/       Session snapshot、CompactionCheckpoint
└── terminal.py    Console / RichConsole
```

核心数据流：

```text
ZhihuOpenAPIClient.get_hot_list()
        ↓ HotTopic(title, summary, url)
build_domain_query() → BGEEmbeddingProvider.embed_texts()
        ↓
rank_domain_matches() → build_daily_plans()
        ↓
每位作者的 DailyPlan.hot
```

Agent 也可以直接调用 `route_hotspots` 获取上述作者侧候选队列；当前仍是 domain-only 召回，不负责生成、评审或发布。

## 快速开始

### 1. 安装依赖

项目使用已有的 Conda 环境 `deepcode` 示例：

```powershell
conda activate deepcode
pip install -r requirements.txt
```

BGE-M3 Provider 使用本地 Hugging Face 缓存并以离线模式加载，不会在运行时重复下载已存在的模型。

### 2. 配置本地环境变量

在根目录创建 `.env`（该文件已被 Git 忽略）：

```dotenv
DEEPSEEK_API_KEY=你的 DeepSeek API Key
ZHIHU_ACCESS_SECRET=你的知乎开放平台 Access Secret
PERSONCLONE_BASE_URL=http://127.0.0.1:8000
PERSONCLONE_SESSION_COOKIE=你的 PersonClone 登录 Cookie
CODEX_PRODUCER_TIMEOUT_SECONDS=1800
DATABASE_URL=sqlite:///data/creatoros.db
```

不要把 Key、Token、Cookie 或密码提交到 Git。PersonClone 服务需要先独立启动；图片生产还需要本机完成 `codex login`，CreatorOS 复用该登录态，不读取或保存认证文件。

### 3. 启动当前 CLI

```powershell
python .\main.py
```

主菜单“今日运营”可以用自然语言新增或调整栏目选题，并在 Preview 后确认；“运行记录”可从选题队列发起 Codex 生产、恢复中断、提出返工并批准精确产物；“Agent 对话”用于体验通用 Runtime。

## 验证

本地纯数据 smoke：

```powershell
conda run --no-capture-output -n deepcode python -m tests.smoke_content_planning
conda run --no-capture-output -n deepcode python -m tests.smoke_codex_producer
conda run --no-capture-output -n deepcode python -m tests.smoke_content_storage
conda run --no-capture-output -n deepcode python -m tests.smoke_operation_plan
conda run --no-capture-output -n deepcode python -m tests.smoke_pending_operation_service
conda run --no-capture-output -n deepcode python -m tests.smoke_pending_operation_cli
conda run --no-capture-output -n deepcode python -m tests.smoke_content_run_storage
conda run --no-capture-output -n deepcode python -m tests.smoke_content_run_service
conda run --no-capture-output -n deepcode python -m tests.smoke_content_run_cli
conda run --no-capture-output -n deepcode python -m compileall -q main.py creatoros tests
```

真实低成本联调（需要知乎密钥、PersonClone 登录态和本地 BGE-M3）：

```powershell
conda run --no-capture-output -n deepcode python -m tests.live_content_planning
conda run --no-capture-output -n deepcode python -m tests.live_codex_producer
conda run --no-capture-output -n deepcode python -m tests.live_codex_resume_protocol
conda run --no-capture-output -n deepcode python -m tests.live_operation_parser
conda run --no-capture-output -n deepcode python -m tests.live_pending_operation_workflow
```

`live_content_planning` 只读热点与画像；`live_codex_producer` 会真实消费 Codex 用量并生成本地图片，但不会发布到平台。

## 路线图

1. **本地 Web Studio**：优先解决账号/栏目不可见与首次使用迷茫；复用现有服务接通选题、生产观测和图片验收。当前仅完成设计，分步接口、边界与验收见 [Studio 实施规划](docs/studio/SPEC.md)，CLI 仍是现有可用入口。
2. **Agent Eval**：建立小型真实运营任务 Benchmark，以最终数据库/文件状态判定 Task Success，并结合执行轨迹分析工具路径和成功任务 Token 开销。
3. **质量评审**：对知识正确性、图片文字、卡片连贯性和平台文案建立可观测评审结果与 badcase 集。
4. **发布与反馈**：接入小红书审批、幂等发布、效果指标与选题反馈；真实平台能力不可用时先稳定发布接口边界。
5. **热点矩阵增强**：保留 PersonClone 路线，继续验证 perspective 路由和 Agent 工作流 eval，不与自有栏目强耦合。

每个阶段都以一个可运行、可验证、可回退的 Git commit 完成；详细假设、边界和验证记录见根目录 [`SPEC.md`](./SPEC.md) 及各模块 SPEC。

## 项目原则

- **Simple first, abstraction later**：先观察真实痛点，再引入抽象。
- **业务边界优先**：PersonClone 是独立服务，CreatorOS 只消费正式 API。
- **结果可追溯**：完整 Session 与 ToolResult 保留在本地，模型上下文使用受控投影。
- **真实验证**：低频、低成本、无破坏性的接口优先使用真实服务验证；密钥只来自本地环境变量。
- **学习与产品并行**：Runtime 是学习主线，Creator Routing 和内容队列是第一条业务主线。
