# 自然表达 Agent 任务集 v2

这是基于 v1 真实试运行发现的评分问题修订出的独立题集。20 个用户请求仍为 AI 合成改写，不是真实用户原话，也不代表真实用户分布。其余30行是现有宿主兼容占位，不是自然样本；冻结时必须显式选择20个允许题号，不允许选择占位。公开 dev/acceptance 分组不是盲测。

v2 请求及任务夹具保持 v1，改动为：

- B10：正确报告最新目录、不虚称自己新增即可；用户已经说过自己在页面添加，Agent 没有再重复来源不扣分。仍需实际新查询和变化后完整目录证据。
- B11/B12：用户指定的是词对及选题意图，并未限定标题逐字。显式启用 `word_pair_title_flexible`，识别带有“还是”等有限连接词的词对和描述性标题，按不同记录一一对应；错误词对、合并、重复、错栏目/状态或改动旧记录仍不通过。无法可靠识别的合理标题待复核，不假装已通过或失败。B12 的独立语义项明确检查每条正文和对应词对是否匹配、四格意图是否保留；正文不逐字重复英文词但上下文足以确定的也可接受。
- B05/B07/B10 不将特定列表工具设成计分门槛；B41 以一张最终交付图为要求，内部生图次数作为成本诊断。账号隔离、禁止未经授权写入和发布、历史资源保护仍有效。

保留 `docs/agent-eval/natural-v1` 的冻结定义及所有旧批次，绝不覆盖原成绩或自动重跑。v2 的 `dataset_id` 为 `creatoros-natural-benchmark-v2-20261105`，自然专项判分修订 `benchmark-natural-v2-intent`。产品共享 grader 版本仍遵循父 v3.4；完整评分和采证源码由每个 manifest 的逐文件 SHA-256 冻结，版本字段不是源码的替代。v1 和 v2 的题意、评分或源码不同，不能直接混合或将差异视为 Agent 进步。

## 证据与局限

旧 v1 真实批 `data/agent-benchmark-v2/natural-v1-evidence-20261105-a` 保留原结果：B10 的 Judge 因未复述用户已说的来源而给 s2 失败；B12 的程序分因三条实际标题是“词A 还是 词B？……”而给匹配失败。对同批 B12 的原始 before/after 快照仅作新规则只读诊断，记录三条独立词对已入队、旧数据保留及无新增 Run；这不是新一轮真实模型成绩，不会覆盖旧结果。v2 尚未按新规则完整重跑验证。

父集与新集注册摘要对解析后的 JSON 使用排序键、紧凑分隔符、UTF-8 编码计算 SHA-256，不受 LF/CRLF 影响；实际批次 manifest 另锁定冻结文件字节。校验是误用防护而非防篡改签名或系统沙箱。

## 显式冻结和运行

```powershell
python -m creatoros.evaluation.benchmark.run freeze --dataset docs/agent-eval/natural-v2/cases.json --batch natural-v2-example --cases B10,B12
python -m creatoros.evaluation.benchmark.run run --batch natural-v2-example --workers 1
```

真实执行会调用付费模型；视频、发布和外部服务不受数据库事务回滚。对支持的非视频题可按现行契约显式启用 `--nonvideo-guard`；该守卫不是网络或进程沙箱。不同守卫、版本与题集条件不能直接比较。
