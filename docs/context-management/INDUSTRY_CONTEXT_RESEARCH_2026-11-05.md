# 工具结果、上下文与缓存：一手资料调研（2026-11-05）

范围：梳理工具返回过大、上下文信噪比和缓存前缀的公开工程实践，结合 CreatorOS 的既有基线提出待验证方案。本文中的厂商结果仅适用于其描述的产品、模型与测试；不是 CreatorOS 的实测收益。调研不执行模型、不重放业务工具，不修改压缩策略。

## 工具输出：先为任务设计返回，再考虑长度上限

Anthropic 建议围绕任务返回有用信息，使用分页、范围选择、过滤和截断控制大结果，并通过错误和截断标记告诉模型如何进一步查询；也提出 `concise`/`detailed` 等不同粒度。其示例中，精简版可能缺少后续操作需要的 ID，因此详细版仍需提供。其 2025 年文章提及 Claude Code 当时默认工具响应限制 25,000 tokens；这不是所有 Claude 产品或当前所有工具的通用阈值。[来源：Writing effective tools for agents](https://www.anthropic.com/research/writing-tools-for-agents)

OpenAI 2026 年针对 Codex 与 ChatGPT Work 的 harness 文章称，其所描述的系统默认将单次工具输出限制为 10,000 tokens，模型可请求不同限制。Codex 提示指南建议超限时保留首尾并标明中间被省略；这是针对该模型/harness 的建议，并不能证明中间的业务证据不重要。[来源：OpenAI 工程文章](https://openai.com/index/gpt-5-6-frontier-intelligence-efficiency/)、[Codex 提示指南](https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide)

**对 CreatorOS 的判断**：按 `manage_topics`、`manage_plan`、`get_skill_extraction` 的实际调用动作分别设计列表、详情和写回执，比套用单一 10k/25k 数值更契合已观测的数据。保留用于下一步操作的 ID、版本、状态、范围、来源和分页，以及失败/unknown、可恢复入口；大正文附明确引用。回读能力必须验证账号和会话作用域、相同 call ID 的唯一定位，以及证据位于中间和多页时能否找回。截断本身只控制尺寸，不保证任务完成。

## 大中间结果：保留原始数据，模型按需读取

Google ADK 将长期 Session、每次请求的 Working Context、可检索 Memory 和大文件 Artifacts 分层。其文章描述大对象以带名称和版本的引用存在，按需加载，完成后可从工作上下文卸载，避免每轮携带原文。这是 ADK 描述的机制，不证明 CreatorOS 的回读、语义质量或权限边界已达到相同效果。[来源：Google ADK 上下文架构](https://developers.googleblog.com/architecting-efficient-context-aware-multi-agent-framework-for-production/)

Anthropic 建议在长任务中保留轻量引用、按需检索；压缩需要先保证必要信息召回，再削减冗余。其建议清理深层工具调用和结果，但也明确指出过度压缩可能丢失之后才显得重要的细节。[来源：Effective context engineering](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents)

**对 CreatorOS 的判断**：已有账本、模型视图和回读机制可作为起点。可比较“完整最近结果 + 老结果保留范围/摘要/引用”“列表索引 + 精确详情”“按会话结果搜索 + 小范围读取”。不能将简单的首尾截取当作信息无损；比较应包括原文找回、无结果与未搜索完的区分、追问正确率及额外往返。涉及写操作要保留身份、授权、CAS、结果未知和失败信息，原账本、Run 冻结输入及归档不随模型视图裁剪。

## 程序化聚合和按需发现：只在适合的任务使用

Anthropic 的 Programmatic Tool Calling 让代码执行环境接收和处理多个中间工具结果，模型只接收最终聚合。其文内复杂研究任务平均从 43,588 降至 27,297 tokens（约 37%），是该厂商内部任务统计；文章同时指出单次小查询、模型必须审查每个中间结果时可能不划算。该能力需要在 Claude 平台明确配置，不是 DeepSeek 自动提供的功能。[来源：Anthropic advanced tool use](https://www.anthropic.com/research/advanced-tool-use)

同文的 Tool Search 可延迟加载完整工具定义，先保留搜索入口和少数常用工具。特定 50 多工具示例上下文从约 77k 降至约 8.7k，文章称降幅约 85%；搜索本身增加步骤、延迟和漏召回风险，且这些数字不能直接外推。OpenAI 也介绍其特定 Agents API 中的工具搜索与程序化调用，两种均为对应平台的可用能力。[来源：Anthropic](https://www.anthropic.com/research/advanced-tool-use)、[OpenAI Agents API](https://openai.com/index/introducing-the-agents-api/)

Google ADK 2.0 的 workflow 把确定性路由交给代码，LLM 处理需要语义判断的节点，减少不必要的中间调用。其博客的 5,152→2,265 token 示例明确使用模拟 API，是说明性 benchmark，不代表生产效果。[来源：Why we built ADK 2.0](https://developers.googleblog.com/why-we-built-adk-20/)

**对 CreatorOS 的判断**：批量查询、状态聚合、明确的只读过滤可评估宿主聚合；不要为了少一次模型调用而隐去失败、未知状态、逐项差异或授权边界。当前真实基线的计划管理含真实版本演进和可能的失败状态，不能把 22 次调用直接判作冗余。Schema 分层在本项目有约 1.4 万估算 token 的潜力，但应同时测试工具漏召回、跨域任务、额外请求和缓存命中，不宜先假设动态路由必然更省。

## 缓存与上下文不是同一个优化目标

OpenAI 描述其 Codex/ChatGPT Work harness 将可见历史按追加方式组织、工具确定性排序，以提高前缀复用；这可减少重复前缀的处理成本，但不会自动降低模型看到的上下文长度。[来源：OpenAI 工程文章](https://openai.com/index/gpt-5-6-frontier-intelligence-efficiency/)

DeepSeek 当前官方文档称缓存默认开启，缓存命中需要与已经持久化的完整前缀单元匹配；前缀单元可在请求边界、识别到共同前缀、长输入/输出固定间隔处生成。其系统为 best effort，缓存构建需要数秒，不再使用后通常在数小时至数日清理，命中不保证。官方 usage 中分别提供 `prompt_cache_hit_tokens` 与 `prompt_cache_miss_tokens`。因此文本看起来相同或相邻快照共享前缀，并不能保证当次命中；需要以实际 usage 为准。[来源：DeepSeek Context Caching](https://api-docs.deepseek.com/guides/kv_cache/)

DeepSeek 官方思考模式文档还要求：请求带 `tools` 时，之前仍在请求中的 assistant 历史 `reasoning_content` 必须完整回传；不能简单删除保留消息内的思考字段来省 token。若用完整旧轮次或完整工具步骤整体替换为摘要，则属于不同上下文投影，仍需独立验证 API 协议及任务连续性。[来源：DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)

**对 CreatorOS 的判断**：固定系统和工具 Schema、避免中间插入变化的业务数据有助于前缀稳定，但账号状态刷新、旧结果重投影、压缩/摘要、工具集合或模型设置变化可能改变前缀。是否实际 miss 还受服务端持久化、时间和缓存可用性影响；需使用实际请求 snapshots 与 usage 对账，不能仅凭源码推断。为追求缓存命中而保留过期数据会损害业务正确性，任何布局改造都要先确保模型看到当前权限、状态和版本。

## CreatorOS 的历史缓存观测

### 样本与方法

只读扫描 `data/creatoros-agent-sessions` 中含 `view.json` 的会话，选 `messages.context-trace.jsonl` 中已结束的主请求：要求实际 `usage.input_tokens`、`cache_hit_tokens` 均为整数且对应 request snapshot 可读取。得到 **34 个会话、738 次请求**，时间为 2026-09-13 至 10-30（北京时间）。这些是历史版本、非随机样本；正式目录也可能混有手工实验或测试，不能推断全部是日常生产或代表当前服务。

- 累计实际输入 38,913,794、命中 30,972,160，按 token 加权约 **79.6%**；每个请求的命中率中位数为 **95.9%**。这两个指标的分母不同，不能互换；仅 2 次请求命中为零不等于其他请求都高命中。
- 同会话按记录顺序对相邻**可用快照**配对，共 704 对；若中间请求缺快照，配对并不一定是紧邻的真实请求。逐项比较 `context.messages`，分类为纯追加或首个不同消息的类型；另行比较 `context.tools`，不把两者当互斥分类。命中率是后一次请求的实际 hit/input 的请求级中位数，而非按 token 加权。
- 快照是 Provider 转换前的 ModelContext，不是服务端实际 token 化结果或完整 HTTP 抓包；看不到服务端持久化单元、缓存逐出、其他历史请求对应的缓存。以下是相关性而非因果或节省量。

| 相邻快照首个差异 | 对数 | 后次请求命中率中位数 | 同一用户轮 / 跨轮 |
| --- | ---: | ---: | ---: |
| 消息纯追加 | 457 | 98.4% | 382 / 75 |
| 账号目录 | 144 | 29.8% | 44 / 100 |
| system/developer | 20 | 13.2% | 0 / 20 |
| assistant 历史 | 77 | 95.9% | 未细分 |
| 本问附加资料或图片 | 6 | 36.4% | 未细分 |

账号目录首差的 144 对中，历史快照有 125 对仅顶层 `as_of` 变化，另 5 对同时有 `as_of` 和业务字段变化、14 对为业务字段变化；仅 `as_of` 变化中有 57 对相隔不超过 60 秒。当前 `creatoros/web/account_context.py:112-115` **已无 `as_of` 字段**，所以不能把旧时间戳当作当前问题。

历史示例：会话 `161b46ed-f656-486f-9606-fa661e12cbc6` 的 `a206d7396d1949e8a9a770ed20d9d5e1` → `3efcff7448eb4216b9aaa1c3aa219321` 跨轮相隔 46 秒、Schema 不变，账号目录仅 `as_of` 不同；hit/input 从 51,456/53,924 降到 10,880/54,081。另 `be3b6b30-f02b-4598-87f7-88ab63cea193` 的 `08c73e9453234f9b8162fca01634e720` → `f858c33ba3934619929d948690ea27e5` 相隔约 20 秒，hit/input 从 38,656/53,113 降至 10,880/53,244，也呈相同首差。间隔短削弱长期过期解释，但仍无法排除服务端缓存状态或 Provider 转换因素。

### 当前代码的前缀风险

1. **账号目录在长历史之前**：`creatoros/agent/loop.py:120-129` 把当前账号目录放在 system 之后、对话之前；新用户轮刷新，部分成功写工具后也刷新（`:262-277,444-449`）。当前目录没有每轮时间戳，栏目、Skill、权限、版本或顺序实际变化时仍可能影响后面所有历史的前缀。账号状态必须新鲜，不应为了缓存隐去变化。
2. **本问临时资料会移动**：账号请求默认附当前生产能力和音乐索引（`creatoros/web/chat.py:973-1000`），`build_model_context` 每次将其追加到消息尾部但不写账本（`creatoros/agent/loop.py:130-134`）。同一轮下一次工具步骤，新的 assistant/tool 记录出现在原先临时资料的位置，临时资料移到末尾。历史会话 `cb1cdd58` 的 `33b674a3` → `acd22f10` 实际快照第 21 条即出现 focus→assistant 的变化；后次 hit/input 为 51,968/64,395。这证明重排存在，不能仅凭快照计算造成的损失。
3. **system、Schema 与历史投影**：`creatoros/web/chat.py:960-972` 会在指令或本轮 RSI 模式变化时重写首条 system，原 checkpoint 校验可能失效而回退完整历史；压缩、投影规则变化及 `creatoros/ai/context.py:35-53` 的旧图片占位也可能改动早期内容。工具来自 `creatoros/tools/definitions.py:290-292` 的有序注册表，`host_contract.py:26-29` 仅按白名单过滤；没有证据表明 `frozenset` 导致随机排序。但不同 scope、续接工具集、定义版本变化可能改变 Schema。服务端 tools 与 messages 的内部拼接顺序不可见，不应断言某次 Schema 改变必然导致全部未命中。
4. **服务端因素**：缓存要先持久化且 best effort；短间隔、服务端状态、模型/请求方式改变以及闲置回收都可能影响命中。代码读取 DeepSeek 返回的 hit/miss（`creatoros/ai/deepseek.py:110-123,198-206`），但不能从使用量反推出唯一原因。

## 建议验证顺序

1. 沿用固定历史基线做可复算的**离线体积对照**，逐动作比较结果大小，但不以此宣称任务成功或厂商实际费用下降。
2. 选隔离、无副作用的真实任务，对同一初始状态重复比较：完整列表/精简列表及按需详情、Skill 状态/正文分离、计划写回执。记录正确率、来源找回、步骤数、实际输入与输出、hit/miss、耗时、额外回读和摘要费用。
3. 使用失败、unknown、过期版本、跨账号、分页中间证据和续接作为约束测试；结果未知时不得重放写操作。
4. 若上述结果仍显示历史过大，再评估完整步骤或轮次摘要、目标驱动工具发现和程序化聚合；同时比较高召回与压缩收益，不把某厂阈值或宣传数字当目标。
