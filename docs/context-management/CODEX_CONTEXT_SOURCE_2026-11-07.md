# Codex CLI 上下文：源码与本对话元数据（2026-11-07）

## 范围

本记录对照 OpenAI 官方公开 Codex CLI 源码提交 [`c1c07853fe6e8e712f264030a30758cfa80eedfe`](https://github.com/openai/codex/tree/c1c07853fe6e8e712f264030a30758cfa80eedfe) 和本条 Codex Desktop 对话的本机 rollout 元数据。源码提交不是经验证的当前桌面二进制源版本；日志仅含本条对话的实况，不代表其他模型或对话。没有输出聊天、工具参数、返回正文或凭据。用户要求本阶段不调整 CreatorOS 压缩策略，本记录中的压缩仅用于解释 Codex 的历史计数。

## 本条对话的变化

只读 `C:/Users/13779/.codex/sessions/2026/11/05/rollout-2026-11-05T01-49-29-01a1a7ae-2181-7ea2-aba0-82bee83ee1ab.jsonl`（本机私有文件，不包含在仓库）。解析每行 JSON：以 `event_msg.payload.type=task_started` 顺序计用户 task，`event_msg.payload.type=token_count` 的 `info.last_token_usage.input_tokens` 记最近一次 usage；每次 `task_complete` 输出最近的值；检测 `type=compacted`，记录压缩前最后一个 usage 和其后第一个 input 非零的 usage。时间直接使用日志 UTC。统计没有读取或输出内容字段，不在此保存原始 rollout。`task` 是一次运行，不保证与界面可见用户消息一一对应；usage 仅代表最近一次报告，并非该 task 的累计费用。

| 位置 | 日志时间 UTC | 最近 usage 的 input tokens |
| --- | --- | ---: |
| 第 1 task 结束 | 2026-11-04 17:52:46 | 61,363 |
| 第 3 task 结束 | 2026-11-05 13:04:35 | 136,963 |
| 第 5 task 结束 | 2026-11-05 15:15:23 | 293,107 |
| 第 9 task 结束 | 2026-11-06 11:40:26 | 336,741 |
| 第 10 task 压缩前最后一次 usage | 压缩标记前 | 449,684 |
| 第 10 task 压缩标记 | 2026-11-06 12:14:20 | 替换历史记录 17 项 |
| 压缩后第一条 input 非零的 usage | 2026-11-06 12:14:23 | 32,165 |
| 第 10 task 结束 | 2026-11-06 12:18:58 | 62,328 |
| 第 12 task 结束 | 2026-11-06 16:29:21 | 111,107 |

压缩前最后 usage 显示该时模型窗口 475,000；压缩前后两次并非同一条请求，不能把差值当作可重复压缩率。日志 `session_meta` 记为 `Codex Desktop`、CLI `0.166.0-alpha.6`，所读 `turn_context` 记模型 `gpt-6.1-terra`、high；客户端和模型均不同于 CreatorOS 的 DeepSeek。这个证据说明本对话窗口曾增长至约 45 万、随后被压缩；不能断言用户先前看到的 6–7 万来自何时或界面的具体计数机制，更不能据此比较费用。

## 官方源码中的工具结果和历史

- 工具返回先作为调用结果进入上下文处理；[history.rs](https://github.com/openai/codex/blob/c1c07853fe6e8e712f264030a30758cfa80eedfe/codex-rs/core/src/context_manager/history.rs#L605-L735) 在构建活动历史时针对每个 `FunctionCallOutput` / `CustomToolCallOutput` 应用截断并追加；同一段源码注明保留 rollout 原始数据。因此，它也会累积历史工具结果，不应描述为“上一轮工具结果自动消失”或断言所有原始结果无条件都可从模型直接回读。
- 对 MCP 结果，[context.rs](https://github.com/openai/codex/blob/c1c07853fe6e8e712f264030a30758cfa80eedfe/codex-rs/core/src/tools/context.rs#L225-L258) 在生成模型可见 payload 时截断，同时源码注明 Code Mode 可接收原结果；脚本可以筛选结果，再输出需要的部分。日志、工具原始结果和模型收到的结果不是同一个口径。[截断实现](https://github.com/openai/codex/blob/c1c07853fe6e8e712f264030a30758cfa80eedfe/codex-rs/utils/output-truncation/src/lib.rs#L20-L69) 对超长文本保留片段并提示截断，不是语义提取，也不能保证中间关键证据保留。
- 命令工具的 [`max_output_tokens` 默认值为 10,000](https://github.com/openai/codex/blob/c1c07853fe6e8e712f264030a30758cfa80eedfe/codex-rs/core/src/unified_exec/mod.rs#L70-L76)，具体模型可见输出还受模型截断策略限制，取二者较紧者，见 [ExecCommandToolOutput](https://github.com/openai/codex/blob/c1c07853fe6e8e712f264030a30758cfa80eedfe/codex-rs/core/src/tools/context.rs#L526-L543)。本机当前 `models_cache.json` 中 `gpt-6.1-terra` 的截断配置为 10,000 tokens；缓存可能更新，不能反证上表当时的配置，也不能推广到其他模型或所有工具。
- 工具描述可以延迟加载：源码中的 [tool_search](https://github.com/openai/codex/blob/c1c07853fe6e8e712f264030a30758cfa80eedfe/codex-rs/core/src/tools/handlers/tool_search_spec.rs#L80-L98) 使用工具元数据检索。OpenAI 的 [Responses tool search 文档](https://developers.openai.com/api/docs/guides/tools-tool-search)指出按需加载与追加位置有助于减少工具定义占用和保留缓存前缀，但有模型与 API 支持条件；不能认为 DeepSeek 的接口具备同等机制。
- 历史中的 `Reasoning` 也是独立项目；源码估算中对带 `encrypted_content` 的 reasoning 有专门处理，[不能把它等同于明文 `reasoning_content` 字符数](https://github.com/openai/codex/blob/c1c07853fe6e8e712f264030a30758cfa80eedfe/codex-rs/core/src/context_manager/history.rs#L1371-L1374)。DeepSeek [thinking mode 官方说明](https://api-docs.deepseek.com/guides/thinking_mode/)明确：请求携带 `tools` 时须在后续请求回传此前各轮 `reasoning_content`，且该内容计入上下文；无 tools 参数的情况不同。不能以 Codex 的接口机制推断 CreatorOS 可以安全丢弃 DeepSeek 推理。
- [OpenAI 缓存文档](https://developers.openai.com/api/docs/guides/prompt-caching)要求前缀匹配；模型、工具描述或顺序等变化都可能影响命中。缓存命中不等于对应 token 从上下文窗口消失；不能套用 OpenAI 的服务端规则计算 DeepSeek 命中率或价格。

## 与 CreatorOS 现有证据的关系

CreatorOS 的 [历史基线](CONTEXT_BASELINE_2026-11-05.md) 中，三例分别有 10/4/8 个用户轮，但有 25/35/40 次主模型请求；每次又会带入先前历史。某 Skill 提取样本的 `get_skill_extraction` 在历史快照中反复返回相同结果，旧样本的工具结果占用是估算值。**这些是历史版本事实，不能直接断言当前仍发生**：当前已有限制同一用户轮内重复读取 pending 任务的逻辑，应以现版本隔离复刻和实际模型可见输出确认。

值得进一步检查的是每次进入模型的结果有多少是相同内容、状态未变时是否反复附带大正文、模型是否持续发起相同读取，以及大段工具参数是否重复；需要保留错误、状态变化、任务身份、实时权限和 unknown 回执。把结果按字段裁剪或在不改变旧前缀的前提下引用已发送证据，是候选方案，实际 token 与任务效果必须由当前版本测试决定。

### Skill 提取样本：具体不能直接推断什么

旧样本的末次请求中，`get_skill_extraction` 结果估算占 35,235 tokens；离线检查发现 25 次完全相同结果，估算重复 32,166 tokens。这是**历史请求快照中已有的内容**，不是现版重跑结果，也不是精确厂商 token。它是高优先级复刻对象，而不是断言现版仍会浪费同样数量的依据。

现版已有两层相关保护：

1. `PendingTaskReads` 在同一用户轮中对可识别的 pending job 复用已读回执；再次读取时标记 deferred，主循环在配对完工具结果后结束本轮，避免模型无限重复轮询。回执仍被复制并以 tool 消息写入账本，而 `PendingTaskReads` 每个用户轮重建；它不是全会话去重，也不禁止跨用户轮读取实时状态。
2. `project_tool_messages` 已对 `get_skill_extraction` 的特定长文本字段做模型视图去重：在身份、页/修订/操作、字段位置、全文等严格相同，且调用 ID 可唯一定位时，以本次请求内此前可见的工具结果作引用。来源不可见时保留正文，错误不走该去重。它不是对整个结果去重：元数据、未覆盖字段、不同版本和不同文本仍可能重复，合法的 tool 调用与参数也仍保留。

追加离线核对：在本轮针对这一份历史快照运行**现版** `project_tool_messages` 后，仍发现 25 次完全相同的结果 JSON，估算占 9,975 tokens；这 25 次的参数 `job_id` 与返回 `id` 一致，状态为 `running`，错误为空。该数字是现版对旧账本的离线模型视图估算，不是现版在线循环或可节约的精确计费量，也不能把 25 次都称为新鲜实时查询。

因此更安全的下一步是把该历史请求**只读地投影成现版视图**，量化现有规则已覆盖哪些字段、剩余占用在哪里，再用隔离当前服务的真实或受控任务确认调用链；不得把离线重投影的减少量写成实时运行收益。对剩余完全相同的稳定字段，才考虑以源结果标识引用；对状态、时间、版本、错误、任务身份或来源可见性不确定的情况保留原样，必须让模型仍能拿到实际变化。重复请求的宿主抑制与返回内容去重是不同问题：减少模型轮询有机会同时减少模型调用，但不能伪造最新状态；跨轮必须允许真实读取。

对已核对的重复项，可考虑仅对 `get_skill_extraction` 成功的 pending 回执在模型视图中引用首次完整结果：请求参数、账号范围、响应 id、原始与投影后的内容均严格一致，且首次结果及唯一调用 ID 在当前请求中可见。当前回执仍需明确保留任务身份、状态与引用，不得省略 unknown、失败或不同状态/版本；缺源或不确定时回退全文。这个候选只去重显示内容，不取消本来需要的真实查询；尤其 `PendingTaskReads` 的 deferred 回执本身是复用的旧结果，不可描述成刚发生过一次实时查询。由此节约多少、模型是否正确理解引用和后续回读，需另行验证。

缓存上，优先让**新出现的**重复结果引用此前完整来源，让已发送部分保持不变；不要随着对话变长回头改写前缀。现有投影会保守处理调用 ID 重用及不完整来源，压缩或历史投影变化也可能使引用必须回退全文；这些安全回退都应纳入跨轮前缀与回读测试，不能把“能引用”视作永久缓存命中。

Codex 的逐工具结果预算与 Code Mode 给了“有界输出、按需筛选”的设计线索，但上述本机对话同样累积到约 45 万才压缩，不支持“Codex 天然不会增长”的结论。两边使用不同模型、不同工具、不同任务和不同推理协议，只有用各自实际请求中的分项、工具往返与同任务受控对照，才能定位 CreatorOS 的真实冗余。
