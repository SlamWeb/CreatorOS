# 工具列表与详情收敛：2026-10-31

本轮解决“目录查询夹带全文、回执重复输入、没有变化却重查”的问题。不新建缓存、不裁剪对话、不修改生产输入，也不合并委派工具。

## 模型看到的变化

| 入口 | 本轮处理 | 仍然保留 |
| --- | --- | --- |
| manage_topics(list) / 旧list_series_topics | 已入队条目仅索引与160字符brief_preview；长预览明确brief_truncated | 全部条目、分页、身份、状态、管理/生产权限；待选候选的完整选择证据 |
| manage_topics(get) | 不裁剪完整制作要求 | brief、sources、edit_digest、具体制作任务 |
| manage_plan(create/update) | 不再次回显每步arguments | 真实步骤、依赖、状态、结果摘要、错误、恢复权限与计划版本；get/resume仍给完整参数 |
| start_content_run批量回执 | 共同Skill摘要提到外层，去同一父选题重复及可选空值 | 每项task/run身份、顺序、状态、独立Skill差异、错误及入口 |
| get_creator_tasks | 只去null/空集合和严格相等的身份、时间、入口别名 | 有意义的差异、失败、汇总、采样时间与筛选范围 |

工具说明同步到真实账号宿主schema：同范围的完整列表、没有已知增删改时复用；没读完继续翻页，最新进展或已知变化则重新读取。查一个已知选题不先扫全账号。Skill库已有对应目录时不重列。

也检查了栏目、Skill库、素材集、BGM：栏目描述用于选择、Skill版本/权限/digest用于操作、素材顺序/checksum用于交接、BGM显式分析用于剪辑，均不一刀切删除。本轮没有改变它们的业务契约。

## 本地与HTTP验证

- 新增9项契约测试，覆盖36条索引、完整详情、待选证据、分页、预览标识、投影幂等、原始证据不变、计划动作区分、批量差异与任务身份。
- 其中计划写回执→显式get使用真实loopback HTTP、独立SQLite和真实工具执行入口，无LLM、无生产。测试的本地Producer未调用。
- 纯字段转换允许本地DTO；不能据此声称模型选择准确率。新模块加入零付费reliability allowlist，paid live脚本仍需显式运行。
- 独立只读复核未发现可行动缺陷。
- 最终定向联合79项通过，包含投影/Skill正文与批量编辑、账号宿主、选题契约、制作回执、计划执行和后台续接。续接故障注入会打印controlled unavailable provider，测试按预期通过。

扩大测试发现两项既有计划夹具失败：`test_partial_update_keeps_skipped_history_and_continues`使用不符合schema的Skill ID `missing`；`test_scoped_replanning_cannot_replace_unknown_production`省略必填制作brief。恢复本轮修改前的投影再次运行，仍是同样两项422失败。本轮不掩盖失败、不放宽生产参数校验，也不宣称完整CI通过。

## 旧Trace离线回放

同一份冻结的历史消息分别用修改前工作副本和新投影转换，原JSON未修改。字符数可精确复算；token是仓库本地估算，非供应商计费对照。

| 样本 | 旧字符数 | 新字符数 |
| --- | ---: | ---: |
| 英语账号36条已入队列表 | 55182 | 25939 |
| 同问63条账号任务目录 | 25204 | 20668 |
| QA账号30项批量制作回执 | 12016 | 8659 |
| 旧计划create回执 | 5480 | 743 |
| 旧计划update回执（之一） | 6447 | 1582 |

英语样本全部67条历史消息的本地估算由78656降到67402；QA样本由48659降到45753。没有删除用户消息、工具调用参数、模型推理或图片。Tool schema仍稳定提供，基础工具定义开销没有在本轮解决；不是把累计input token当作单次上下文。

证据：`tmp/tool-index-context-20261031/{offline-replay.json,plan-offline-replay.json}`；原英语session `cb1cdd58-2f46-499a-a3bc-88eed41c4525`，request `22fb46f495414d99bd4b20a033b029aa`。

## 真实前端DeepSeek

将上述冻结列表的36条标题/brief复制到独立SQLite，使用新身份和手工来源。仅在目标末尾增加一条位于预览之外的核对约束。查询在调用前保存到setup.json；未修改正式数据、旧产物、RSI或分数。模型和前端均为真实服务，无mock LLM；没有调用Codex，因为本轮没有生产变更。

| query目标 | 预期 | 实际 |
| --- | --- | --- |
| 列出英语辨析全部已入队标题 | 读取完整目录，保留36条 | manage_topics(list,state=queued,limit=100)一次 |
| 再建议一个没写过的形近词选题 | 复用完整列表，只建议 | 零工具调用 |
| 读取show/indicate/demonstrate/reveal完整制作要求和最后约束 | 直取该身份的详情 | manage_topics(get,index-topic-00)一次，正确指出“小蓝点” |

最终session：`b6cfc53f-210a-4fa2-a5f9-4a91d83542a6`。三问共5次真实模型请求；累计输入136062、输出1708，cache hit124416、miss11646；最后一次input29905。这里没有把两次不同模型运行当成受控节省率对照。

数据库预期/实际：36条选题不增不减，标题/brief逐字一致，业务行SHA256均为`7ae3c76e1aab9ca47b5feea879995911466244069286114bb792534099248082`，Run数0→0。浏览器页面错误0。证据：`tmp/tool-index-live-20261031b/{setup.json,browser-report.json,database-check.json,chat.png,chats/}`。

失败记录没有删除：初次建夹具误以为冻结列表含confirm词组，启动前即失败；首次前端第二问遭Windows `view.tmp→view.json` WinError5，未进入模型请求。后续两个探针等待条件误用了当前dist中不存在的状态/禁用行为，属于测试脚本问题，已改为观察真实任务状态。失败报告和对话均在同一隔离目录保留。Windows会话文件替换问题不在本轮改动的工具投影调用栈内，作为独立可靠性问题留下，不能据一次重跑通过宣称解决。

## 复跑

```powershell
python -m unittest tests.test_tool_index_context tests.test_model_projection
python -m tests.live_tool_index_server --root D:/CreatorOS/tmp/<fresh-root> --trace <frozen-request-json> --port 8898
node web/e2e/live-tool-index.mjs http://127.0.0.1:8898 D:/CreatorOS/tmp/<fresh-root>
```

第二条命令使用本地.env的真实DeepSeek凭证启动隔离服务；第三条从网页发送三问，付费调用不会加入默认CI。需要已构建的web/dist与Chrome；冻结JSON需含本次36条列表。正式服务本轮未重启，新投影尚未加载到8765进程。
