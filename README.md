# CreatorOS

面向内容创作者矩阵的自治运营 Agent。CreatorOS 目前也是一个从零构建的 Python Agent Runtime 学习项目：先把模型调用、工具执行、上下文与会话等底层能力做小、做实，再逐步接通创作者运营业务。

> 当前阶段：已打通 Agent Runtime、知乎热榜/搜索、PersonClone 路由画像和作者侧热点队列；内容生成、质量评审、发布与效果反馈仍在逐步接入。

[源码仓库](https://github.com/SlamWeb/CreatorOS) · [Pi Agent 参考实现](https://github.com/earendil-works/pi) · [Pi 文档](https://pi.dev/docs/latest)

## 产品目标

CreatorOS 的长期目标是把创作者矩阵运营编排成一个可恢复的 Agent：

```text
热点发现 → 热点拆解 → Creator Routing → PersonClone 生成
        → 质量评审 → 人工审批/自动发布 → 效果反馈
```

运营者最终只需要配置作者、日更目标和发布策略；系统负责为每个作者维护热点、常青和实验三个内容队列，并把异常交给人工处理。

## 当前已实现

- **Python Agent Runtime**：Provider 抽象、Agent Loop、Tool Calling、Pydantic 参数校验、结构化 `ToolResult`、MaxTurnGuard。
- **流式终端体验**：DeepSeek SSE 流式响应、Rich Markdown 输出、底部单行工具状态栏、Windows 终端稳定的单向追加渲染。
- **上下文与会话**：`AgentState`、`RuntimeContext`、`ModelContext`、token 预算、自动/手动压缩、`CompactionCheckpoint`、大型 ToolResult 投影和按 `result_ref` 分页读取。
- **知乎数据接入**：官方热榜 API 和站内搜索 API，映射为内部不可变数据模型；只从环境变量读取 Access Secret。
- **PersonClone 接入**：通过独立 FastAPI 服务读取作者列表和 `AuthorRoutingProfile`，复用登录 Cookie；CreatorOS 不读取 PersonClone 本地文件、Qdrant 或原始语料。
- **Skill Loader**：递归发现 `creatoros/skills/**/SKILL.md`，解析名称和描述并注入模型上下文；完整 Skill 正文按需读取，暂不自动执行脚本或持久化激活状态。
- **Creator Routing**：对作者 domain prototypes 与热点标题/介绍使用本地缓存的 BGE-M3 生成向量，并按 `corpus_version` 与文本指纹复用作者原型向量，以作者内部 Max Similarity 完成第一阶段候选召回。
- **作者侧内容队列**：把热点→作者的匹配矩阵反转为每位作者的 Top-N `hot` 队列；`evergreen`、`experiment` 队列的数据结构已预留。
- **Agent 可调用路由**：`route_hotspots(limit, top_k)` 将实时热榜、PersonClone 画像、离线 BGE-M3 和作者侧队列编排为一个 Tool；失败或不可用画像会被结构化报告，不读取 PersonClone 本地文件或 Qdrant。
- **route-and-answer Skill**：用 `SKILL.md` 描述“热点匹配→选择作者→生成回答”的流程；普通 Agent 只看到 `read_file`、`route_hotspots`、`ask_author` 等原子工具，Python Runner 作为宿主侧自动化入口保留。

当前路由结果是候选召回，不是最终发布决策；宽泛领域原型、跨域视角和最终 LLM 重排会在后续切片中单独验证。

## 架构概览

```text
creatoros/
├── ai/            Provider、DeepSeek、ModelContext、流式类型
├── agent/         Agent Loop、State、Guard、Compaction
├── tools/         Tool、Registry、Pydantic Args、ToolResult
├── discovery/     HotTopic、知乎搜索/热榜领域模型
├── integrations/  知乎 OpenAPI、PersonClone FastAPI Client
├── routing/       Profile 模型、投影、BGE-M3、domain 召回
├── planning/      ContentOpportunity、DailyPlan、作者侧队列
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
```

不要把 Key、Token、Cookie 或密码提交到 Git。PersonClone 服务需要先独立启动并保持可访问。

### 3. 启动当前 CLI

```powershell
python .\main.py
```

当前 CLI 入口主要用于体验 Agent Runtime；作者队列的业务 UI 仍在建设中。

## 验证

本地纯数据 smoke：

```powershell
conda run --no-capture-output -n deepcode python -m tests.smoke_content_planning
conda run --no-capture-output -n deepcode python -m compileall -q main.py creatoros tests
```

真实低成本联调（需要知乎密钥、PersonClone 登录态和本地 BGE-M3）：

```powershell
conda run --no-capture-output -n deepcode python -m tests.live_content_planning
```

真实联调只读取当前热榜、作者路由画像并生成内存中的候选队列，不调用数字分身生成和平台发布接口。

## 路线图

1. **队列预览与选择**：在 Rich CLI 展示每位作者的三个队列，支持从 `route-and-answer` 候选快照选择一个热点进入生成流程。
2. **热点增强**：对选中的热点按需调用知乎搜索，补充问题、回答和证据，而不是为所有热点预先爬取全文。
3. **Creator Routing v2**：引入领域层级、阈值和可选的 perspective 原型，再增加基于证据的 LLM 重排。
4. **生成与评审**：调用 PersonClone 生成草稿，增加结构、事实、风格和平台规则评审。
5. **发布与反馈**：接入审批、幂等发布、定时调度、效果指标和可恢复任务状态。
6. **Web 控制台**：CLI 保留为调试入口，网页负责矩阵管理、队列操作、审批和运行观测。

每个阶段都以一个可运行、可验证、可回退的 Git commit 完成；详细假设、边界和验证记录见根目录 [`SPEC.md`](./SPEC.md) 及各模块 SPEC。

## 项目原则

- **Simple first, abstraction later**：先观察真实痛点，再引入抽象。
- **业务边界优先**：PersonClone 是独立服务，CreatorOS 只消费正式 API。
- **结果可追溯**：完整 Session 与 ToolResult 保留在本地，模型上下文使用受控投影。
- **真实验证**：低频、低成本、无破坏性的接口优先使用真实服务验证；密钥只来自本地环境变量。
- **学习与产品并行**：Runtime 是学习主线，Creator Routing 和内容队列是第一条业务主线。
