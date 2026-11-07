---
name: CreatorOS
description: 白底、石墨文字与蓝色 C 标组成的中文创作入口和渐进迁移中的工作台。
colors:
  canvas: "#ffffff"
  surface: "#f5f5f7"
  ink: "#1d1d1f"
  supporting-text: "#62626b"
  line: "#d2d2d7"
  accent: "#0071e3"
  accent-strong: "#0060c9"
  secondary-line: "#cbdff7"
  action-hover: "#f4f8ff"
  brand-blue: "#2277f5"
  brand-light: "#74b7ff"
typography:
  display:
    fontFamily: '"Inter Variable", "Inter", "Noto Sans SC", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif'
    fontSize: "clamp(48px, 6.8vw, 96px)"
    fontWeight: 700
    lineHeight: 1.18
    letterSpacing: "-0.035em"
  headline:
    fontSize: "30px"
    fontWeight: 600
    lineHeight: 1.35
    letterSpacing: "-0.025em"
  body:
    fontFamily: '"Inter Variable", "Inter", "Noto Sans SC", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif'
    fontSize: "clamp(16px, 1.5vw, 20px)"
    fontWeight: 400
    lineHeight: 1.8
  label:
    fontSize: "13px"
    fontWeight: 500
rounded:
  sm: "10px"
  md: "14px"
  lg: "20px"
spacing:
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "32px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.canvas}"
    typography: "{typography.label}"
    height: "36px"
    padding: "8px 18px"
  button-primary-hover:
    backgroundColor: "{colors.accent-strong}"
  button-secondary:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.accent}"
    typography: "{typography.label}"
    height: "36px"
    padding: "8px 18px"
  button-secondary-hover:
    backgroundColor: "{colors.action-hover}"
    textColor: "{colors.accent-strong}"
  button-link:
    backgroundColor: "transparent"
    textColor: "{colors.accent}"
    height: "36px"
    padding: "8px 18px"
  input:
    backgroundColor: "{colors.canvas}"
    rounded: "{rounded.sm}"
    height: "44px"
    padding: "8px 12px"
---

# CreatorOS 设计

本文前半部分是根据两份架构审查形成的**目标设计**，不代表代码已经按此组织；当前行为和迁移验收见 [SPEC.md](SPEC.md)。后半部分记录已确认的视觉身份和工作台设计原则。文件头是设计参考值，并非全站实现现状或统一迁移承诺：实际样式以源码为准。例如当前 `theme.css` 的 `--muted` 为 `#86868b`，与文件头的 `supporting-text` 不同；不要在未检查对比度和受影响页面时直接全局替换。

想从当前代码入手，请先看 [项目地图](docs/PROJECT_MAP.md)；其中区分已落地的模块与仍然耦合的部分。

## 目标：以业务职责组织系统

网页和账号 Agent 在各自授权范围内使用同一套适用的业务规则；底层由相应模块管理状态、事务和外部副作用。发布确认、Skill 候选应用及版本切换等受保护操作保留现有的用户确认边界。

```text
创作者
  ├─ 网页 / HTTP / SSE ─────────────┐
  └─ 账号 Agent / 工具 / 计划 ──────┤
                                    ▼
              账号与栏目 · 选题 · Skill · 制作 · 素材 · 发布
                                    │
                 SQLite / 文件 / 模型与媒体适配器
                                    │
                  Codex、DeepSeek、音视频和平台服务

       Observation / Benchmark：从外侧读取证据、验证行为
```

图中是职责方向，不要求 Agent 必须跨 HTTP 或直接访问数据库。当前部分路径仍经本机 HTTP、部分使用共享实现；改接线时须保持账号作用域、事务和错误语义。

| 业务模块 | 应负责的事 |
| --- | --- |
| 账号与栏目 | 账号、栏目、归属和面向当前账号的可见范围。 |
| 选题 | 选题的内容、状态、来源及调研结果；生产任务引用明确的选题输入。 |
| Skill | 资源库、授权、正文与 assets、版本和任务冻结；提炼与校准使用这些能力。 |
| 制作与交付 | 制作任务及依赖、Run / Revision / Attempt、队列、冻结输入、执行权、接收/验证产物和恢复。 |
| 素材 | 素材归属、素材集、音频、选片、顺序和导出；为制作提供明确的输入引用。 |
| Agent | 会话、上下文、工具和持久计划；计划跟踪自己的步骤，业务结果以对应业务模块为准。 |
| 发布与反馈 | 审批、发布请求/回执及反馈，状态与制作完成分开。 |
| 观察与评测 | 展示 Trace、业务证据与隔离评测结果；不反向成为生产状态的权威。 |

模块名称和未来物理目录仍可随实现调整。公开契约应集中业务约束、失败方式和调用所需信息；只有确有变化或隔离需要时才引入适配层，避免把公共目录变成新的杂物间。后台生产是否单独进程由可测的需求决定。

## 生产与交付的设计要点

- **计划不是生产状态机。** AgentPlan 管自己的步骤和结果观察；ProductionTask 管制作需求及依赖；Run / Revision / Attempt 管执行、版本和技术尝试。它们可以协作，但不能维护互相矛盾的生产结果。
- **数据库原子性与外部副作用分开。** 同一操作需要创建的任务、Run、队列和幂等记录应经可感知事务的应用入口协调；等待依赖的任务可以暂时没有 Run。Codex 调用、文件写入和网络发布的未知结果必须另有核验和适用的恢复策略。
- **共享生命周期，按产物验收。** 冻结输入、记录执行、接收结果、核验证据、更新权威状态；图片顺序、视频编码/音轨和其他类型的语义各自验证，不强塞进完全相同的业务格式。
- **安全和可观测性贯穿入口。** 账号归属、授权与竞争保护在业务操作中生效；Trace 保留排错证据，给模型和用户的结果按任务需要投影，避免无意义的上下文膨胀。
- **以真实边界验证重构。** 对已知的重复生产、结果未知、历史摘要、权限竞争和真实前端交互建立针对性证据；受控测试、真实模型结果和作品质量分别判断。

## 前端视觉与交互

### 视觉身份

保留 CreatorOS 蓝色 C 标、白色画布和石墨正文；工作台用蓝色突出主要动作，次级操作采用白底、浅灰描边和深色文字，按钮悬浮不加下划线。入口主页是中性配色例外：Logo 保留蓝色，标题近黑，唯一「进入工作台」按钮为黑底白字。首页导航不加悬浮下划线、箭头或重复入口。

入口主页采用已确认的两行标题「每个好作品」「都不止于一次」，下方是「把这一次的创作经验，变成下一次的起点。」；标题配黑白场记板与红色拼图交互。首页底部不展示或请求账号列表，账号和栏目在工作台管理；原有直达链接保持可用。

### 工作台

围绕当前账号、栏目、选题、产物和下一步动作组织界面；账号与栏目逐级展开，不把设置和技术记录长时间铺在内容前。近期作品应容易找到，危险操作清楚区分。首页的大标题和大留白不强加给高信息密度的业务页面。

#### 信息如何呈现

- **字体与按钮。** 全站使用 `--font-sans`：英文 Inter，中文优先本机 Noto Sans SC，其次系统中文字体，正文和按钮保持自然字距；中文实际字形随设备上的回退字体变化。标题用近黑和适度字重建立层级，辅助文字用中性灰。Skill 库以页面标题 24px、条目名称 15px、说明和按钮 14px 为当前参考，使用中等字重并以实际中英文渲染和可读性为准。主要按钮用蓝底白字，次要按钮用白底、浅灰细边及深色文字；采用紧凑的圆角矩形和一致的内边距。保留清晰的 hover、键盘焦点及禁用状态，同一决策区域只突出一个主要操作。

- **先看对象，再看操作。** 页面首先让人识别当前账号、栏目或作品；标题、相邻关系和稳定位置共同表达层级。页面切换后仍能看清所属上下文和返回路径。
- **让真实内容承担视觉重点。** 栏目以实际作品和选题为核心，验收以当前版本的真实产物为核心。空、加载和失败分别表达，不用装饰或虚构作品填充。
- **状态紧贴对象与决策。** 待处理、进行中、失败、待批准、已批准及已发布依实际数据展示；状态文字和形状不能只靠颜色区分。批准不等于发布；当前栏目 Skill 与历史任务冻结版本不能混为一谈。
- **一处突出当前主要动作。** 同一决策区域区分主动作、次动作和危险动作；动作层级随状态变化，不用同等强调的按钮淹没作品。设置、历史版本和技术证据可按需展开，但保持可发现、可返回。
- **用布局减少解释成本。** 通过分组、间距、对齐、标题和组件状态表现关系；需要防止误解的业务含义仍保留简短明确的文字。不要用说明段落弥补不清楚的层级，也不要为追求极简隐藏必要反馈。
- **Skill 库辨识。** Skill 库同时展示四个正式分类池，待分类单独收纳。条目使用白底细边，默认只显示名称，可查看详情、点击加入或拖入组合；多分类项目在对应池里共享选择状态。未分类和分类读取失败不能冒充已分类。中文字体当前依赖本机 Noto Sans SC，缺失时回退系统中文字体，不声称跨设备字体一致。

#### 屏幕分享与日常使用

- 工作台按操作界面设计，同时考虑面试屏幕共享：常见桌面视口下优先保证作品、标题、状态和关键动作可辨认，避免双层固定导航长期挤占主要内容。小字号仅用于真正次要的信息；依据真实截图和对比度检查确定尺寸，不把文档中的参考值直接当作全站标准。
- 桌面布局应能容纳真实长标题、多个栏目和密集作品；小屏将导航与侧栏转换为可操作的布局，不遮挡主要内容、焦点或动作。空态、错误、加载、长内容及异步更新都属于设计的一部分。
- 鼠标、键盘和触屏均能完成主要路径；焦点可见，图标有可访问名称，导航使用符合语义的链接，动态结果可被感知。动效尊重减少动态效果偏好。具体实现和验收以 [前端工作流](web/FRONTEND_WORKFLOW.md) 为准。
- Vercel Web Interface Guidelines 是代码审查参考，不替代业务判断或浏览器验收。颜色、字号和布局改动先在代表页面验证，再决定是否沉淀为共享 token 或推广全站。

沿用现有 React、TypeScript、Tailwind 和本地 UI 组件，保留真实状态、加载/失败反馈、键盘焦点和响应式布局；不为更换组件库重写全站。交互和视觉分别验收，具体工作流见 [web/FRONTEND_WORKFLOW.md](web/FRONTEND_WORKFLOW.md)。

### 动效与无障碍

首页标题进入时做一次柔焦显现，场记板、拼图和入口按钮在适当交互时提供反馈，动效不持续循环；触屏、键盘以及减少动态效果偏好均须可用。装饰不遮挡文字和操作，工作台以内容和清晰反馈为主。
