# CreatorOS README 与品牌首页 SPEC

## 2026-10-04 产品首页重写

- 目标：从学习路线首页改为本机自用的创作团队工作台，说明作品提炼 Skill、内容/呈现 Skill 组合、账号 Agent、生产验收与人工发布反馈。
- 只改 README、本 SPEC 和 docs/assets 品牌素材；不改业务代码、正式数据、Codex 登录配置或全局 Skills。
- Logo 复用 web/src/components/Layout.tsx 的蓝色 C 两条 SVG path，不生成另一套标识。横版宣传图采用浅底、蓝色主调与少量彩色点缀，用概念插画表达创作，不伪装成真实 UI 截图。
- 明确区分组合与永久融合、草稿与入库、批准与发布；不承诺任意媒体生产、全局 Skill 完全隔离、自动发布或自动增长闭环。
- 快速开始按当前依赖、启动器、配置和页面入口核验；不绑定开发者的 Conda 环境或要求 PersonClone 服务。
- 验收：图片目视检查与文件可解码；README 相对链接/锚点与配置命令校验；本地 Markdown 渲染检查；git diff --check；独立提交并推送。

### 完成与验证

- README 已重写为产品定位 → 4 项能力 → 安装启动 → 6 步使用 → 架构/本地文件 → 真实边界/工程文档；不再引用旧紫绿图标、学习路线封面或过期的固定两 thread 生产描述。未删除历史资产，未修改其他模块 SPEC。
- Logo SVG 的两条 path 与当前 Layout.tsx 逐字匹配；新封面 1672×941、1,653,189 bytes，可解码，已目视检查。宣传插画不是运行截图或实际质量保证。
- 本地使用仓库既有 remark/Playwright 依赖渲染 Markdown，18 个相对路径/显式导航锚点检查通过；1100×1000 与 390×1000 视口的 Logo/封面真实加载、页面无横向溢出、点击“快速开始”确实滚到目标。已看桌面/手机首屏和手机安装段截图；代码块保留自身横向滚动。证据位于忽略的 tmp/readme-preview/。
- 首次截图未找到当前 Playwright 所需的 headless-shell 二进制；改用本机已安装 Chrome 重跑通过，没有安装新浏览器。此次为 README 的本地近似 GitHub 样式渲染，不宣称已做 GitHub 线上页面像素一致性验收。
- Python SDK 本地包元数据确认 Requires-Python >=3.10；package-lock 的 Vite Node 条件为 ^20.19.0 或 >=22.12.0，README 推荐 22.12+。真实 python -m creatoros.web --help 通过，端口/配置/数据位置与启动器源码核对；没有新建虚拟环境完整重装依赖，也没有启动正式 Studio 或执行数据库迁移。
- smoke_skill_draft_files、smoke_native_production、smoke_account_context 三项通过；受控生产 transport 只检查接线和恢复，不是本轮真实生图质量验证。仅新宣传图调用内置 ImageGen，未调研、生产运营内容、发布或修改正式数据。
- git diff --check 通过；只提交 README、本 SPEC 与两份新品牌素材，保留工作区中的 .codex-remote-attachments/ 不提交。

### 新宣传图生成记录

- 用途：README 首屏横版概念宣传图，不是可操作 UI 截图。
- 内置 ImageGen 模式（工具未公开具体模型名），成品 1672×941、PNG；保存为 docs/assets/creatoros-studio-cover.png，源生成文件不删除。参考图为用户提供的当前 CreatorOS Logo 截图；SVG 图标复用项目自身代码。
- 提示词如下；已目视检查主要标题、中文、副标题与三组标签，浅底与蓝色 C 匹配新品牌，未出现自动发布承诺。旧图片保留，但 README 不再引用。

```text
Use case: ads-marketing
Asset type: CreatorOS GitHub README promotional cover, wide landscape 16:9.
Primary request: Create a polished, minimal, colorful product launch visual for an open-source local AI creator workbench. The user is the creative director; reusable Skills power their creative team.
Input images: Image 1 is ONLY the official brand identity reference. Reproduce its blue faceted open C mark and dark CreatorOS wordmark faithfully. Do not use the old purple/green infinity identity.
Scene/backdrop: warm white paper-like background, generous clear negative space, clean editorial composition.
Subject: An elegant imaginative tabletop creative toolkit on the right: two distinct rounded paper cards with a blue idea/lightbulb glyph and a coral painterly glyph slot together; nearby several small tasteful sample content sheets with miniature illustrations fan out into a coherent collection. This is conceptual brand artwork, not an actual product UI screenshot. A subtle mint and sunny yellow accent suggests multiple creative possibilities. No characters or mascots.
Style/medium: premium editorial 3D paper sculpture mixed with crisp graphic illustration, soft natural shadows, restrained playful geometry. Clear hierarchy, few objects, high craft, not generic neon AI marketing.
Composition: brand and headline on the left, toolkit illustration on the right. Quiet footer with three short capability labels; keep everything legible in a GitHub banner. Avoid tiny text.
Color palette: warm white, near-black text, primary #2277f5 blue, light blue #74b7ff, small coral/mint/yellow accents.
Text (verbatim): "CreatorOS"; headline "你来当老板，AI 负责创作。"; subtitle "把创作方法变成可复用的生产能力"; footer labels "作品 → Skill"  "Skill → 栏目"  "选题 → 产物".
Constraints: accurate Chinese typography; only this requested text; match brand reference; no claims about automatic publication or revenue.
Avoid: dark backgrounds, purple gradients, infinity logos, robots, glowing circuitry, fake dashboard controls, unreadable corner text, watermarks.
```

## 初版记录（2026-09-07）

以下保留历史目标、视觉和验收记录；当前首页以本文件顶部的 2026-10-04 更新为准。

## 目标

- 参考 `learn-to-interview` 的产品型 README 信息层级，为 CreatorOS 建立清晰的 GitHub 首页。
- 用原创图标和横版封面表达“从零写 Agent Loop，到能够构建、运营、恢复并讲清一套 Agent 系统”。
- 让第一次进入仓库的人在一分钟内看懂：它解决什么问题、已经跑通什么、如何体验、为什么值得深入阅读。

## 边界

- 只修改 README、品牌图片和本 SPEC，不改变 Runtime、Studio、数据库或业务行为。
- 已实现能力、保留实验线和 Roadmap 必须明确分开；不声称已经发布、完成长期记忆闭环或提供 MCP Server。
- 品牌图片保持原创，不复制其他项目的图标、构图或视觉资产。

## 视觉方向

- 图标：简洁的 CreatorOS 抽象标记，表现一个小起点进入可恢复的闭环；深色底、柔和紫与薄荷绿、少量暖黄。
- 封面：从左侧最小 Agent Loop 出发，经过 Tool、Context、Memory、Workflow，抵达可观察的 Creator Studio；横版、编辑感、少字、适合 GitHub README 首屏。
- 封面文字只保留 `CreatorOS` 和 `FROM ZERO TO AGENT SYSTEMS`，避免小字和营销口号堆叠。

## 验收

- README 顶部包含图标、封面、简短定位和可点击导航。
- 快速启动命令与当前代码一致，完整能力与未实现边界均可定位。
- 两张图片真实存在、可解码、尺寸适合 README；Markdown 图片路径和内部链接有效。
- `git diff --check` 通过，更新本 SPEC 后独立 commit 并 push。

## 状态

- 2026-09-07：README 已按“结果先行 → 为什么 → 架构 → 学习路径 → 启动 → 当前边界 → 下一步”的产品首页结构重写。
- 使用内置 ImageGen 生成原创 `creatoros-icon.png`（1254×1254）和 `creatoros-cover.png`（1672×941）；本地以原始文件重新打开检查，Logo、标题和副标题清晰，无额外文字或水印。
- README 明确区分当前主线、PersonClone 实验线与 Roadmap；未声称自动发布、MCP Server、完整长期记忆或已完成 Agent Benchmark。
- 本轮只变更文档与品牌图片，不运行模型业务链路、不访问正式数据库、不生成或发布运营内容。
