# 工具 Schema 去除冗余属性标题（2026-11-05）

## 改动

账号、总览和 CLI 继续暴露原范围内的完整工具集合，注册顺序与执行/权限检查不变。宿主在已复制的模型 Schema 上，仅移除与对应属性名可机械互相还原的 `title` 注解（例如 `topic_id` 的 `Topic Id`）；原始工具注册表、实际 `title` 参数、约束、字段、`$defs`、描述、自定义标题、默认值和示例保持。遍历仅进入 Schema 位置，不递归修改默认值或业务数据。JSON Schema 的 `title` 是非校验注解，但能向模型提供提示，因此需要任务验证，不能把校验不变等同模型表现不变。[JSON Schema annotations](https://json-schema.org/understanding-json-schema/reference/annotations)

当前工作树的账户范围工具 Schema，本地字符规则估算由 **16,101 降至 14,987**（减少 1,114）；总览从 16,525 降至 15,381，CLI 从 20,002 降至 18,638。这些不是实际计费用量。Schema 首次变化可能使此前缓存前缀不能复用，是一次部署切换的成本；此后对同版本和 scope，生成顺序固定，但服务端 best-effort 命中不能保证。

Web 账号聊天在 `archive_only_reads` 模式不会默认注入 CLI Skill 目录；CLI 只注入可用 Skill 的名称、描述、路径，正文按需读取。当前本地发现 3 个 CLI Skill，目录估算约 215 token，尚无证据支持为了省这部分而加入动态路由，也不应据此修改 Skill 装载及权限。

## 隔离真实模型探针

使用 `deepseek-flash` 非思考模式，4 组纯合成只读请求：选题详情、计划详情、Skill 正文和失败任务。每组分别使用原始 Schema 与去重 Schema，各一次，交替先后顺序，共 **8 次模型请求**；仅检查工具名和关键参数，并用实际工具参数解析器校验，**没有执行工具**，不访问业务数据库、生产、发布或历史任务。8 次均选择期望的唯一工具且参数通过校验。样本小、非完整业务链路，不代表复杂任务成功率。

| 任务 | 原始 Schema 实际 input | 精简 Schema 实际 input | 两者选择/参数 |
| --- | ---: | ---: | --- |
| 选题详情 | 17,389 | 15,859 | 均通过 |
| 计划详情 | 17,387 | 15,857 | 均通过 |
| Skill 正文 | 17,390 | 15,860 | 均通过 |
| 失败任务 | 17,387 | 15,857 | 均通过 |

每对约少 1,530 实际输入 token；这包含服务端对请求的计量，不单独证明某字段的 tokenizer 计算。两套 Schema 的首次请求分别 cache hit=0 与 128；后续原始/精简版分别观察到 hit=17,152/15,616，表明在该次实验中两套稳定前缀均可被缓存，不能归因完整的缓存行为或保证未来命中。总 input、output、hit/miss 按实际供应商返回，无日志记录密钥或正文。

## 验证与复跑

```powershell
D:/Anaconda4.7g/envs/deepcode/python.exe -m unittest tests.test_tool_schema_context tests.test_host_tool_contract
D:/Anaconda4.7g/envs/deepcode/python.exe -m tests.live_tool_schema_context --live
```

前一命令通过 8 项测试：逐字段证明除机械重复的属性 title 外没有任何差异，原工具集合与排序未变，验证 JSON Schema 合法，保留真实 `title` 属性、默认/示例、自定义注解及账号绑定拒绝越权。后一命令仅显式指定 `--live` 才调用远端；该探针不执行模型请求的工具。两种 Schema 同任务对照不等于完整模型 E2E，也未验证所有工具或所有输入。
