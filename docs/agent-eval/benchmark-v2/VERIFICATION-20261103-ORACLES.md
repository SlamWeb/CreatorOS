# v3.1 判分与采证验证（2026-11-03）

工作树：`merge-benchmark-main/CreatorOS`，分支 `codex/architecture-foundation-docs`。本记录只覆盖判分器修正，不是付费真实模型或浏览器全流程的成绩。

## 修订

- Dataset：`creatoros-trace-benchmark-v3.1-20261103`；冻结修订：`oracle-v31-20261103`；grader：`benchmark-v3.1-case-outcomes`；采证：`skill-working-inventory-v1`。计分汇总算法仍是 `case-rubrics-v3`。
- B15 接受删除/归档的真实回执，精确保留其余选题 ID 与全部字段；不能靠行数或要求整表不变判定。
- B26/B28 正文和资产只取同一成功编辑回执返回的候选；完整 working 文件清单与 after 哈希一致。失败尝试不冒充成功回执，也不阻止后续有效结果被核验。缺失/冲突/未知证据保持待复核。
- B26 原参考图集合与字节保留；B28 保留图字节不变；原版本同时比较 working 与 immutable versions。
- 采证不调用会补建文件的 locate/describe；Skill 引用取 SQL 快照。Judge 只接收实际候选与运行前捕获的原正文，原始正文采证另存 initial_skills.json。
- 旧 data、manifest、报告、分数、正式服务未改动；未创建真实付费新批次，未启动真实模型或浏览器，未 commit。

## 验证

```powershell
$env:CREATOROS_FFMPEG='D:/Anaconda4.7g/envs/llmpc/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe'
$env:CREATOROS_FFPROBE='D:/SelfMedia/管理/工具/视觉工坊/remotion/node_modules/@remotion/compositor-win32-x64-msvc/ffprobe.exe'
& 'D:/Anaconda4.7g/envs/deepcode/python.exe' -m unittest discover -s tests -p 'test_benchmark*.py' -q
```

最终：**131 tests，43.462 秒，全部通过**。包含隔离真实 HTTP world 预检，断言模型调用 0、新 Attempt 0；其余为程序/受控证据回归，不冒称真实模型或完整用户体验验证。任务文件 `git diff --check` 通过。已有 Starlette/httpx 弃用提示不影响结果。

初次系统 Python 缺 sqlalchemy；切换项目文档指定解释器。初次完整回归因缺 ffprobe 及新回执 fixture 的 Skill ID 未满足 DTO 而失败；配置现有工具、修正 fixture 后重跑通过，未安装依赖或改系统环境。

50 题请求、来源、scenario、family、split 等原投影 hash 仍由原锁定测试验证；本轮只调整题目判分与采证。新测后才可报告 v3.1 成绩；Judge 未经人工校准。

## 前后对比约束

集成负责人需将**完全相同的修正题集与整个 creatoros/evaluation/benchmark 运行代码**放入前后产品版本，再分别新建批次 freeze；核对两份 manifest 中评测代码和 frozen-cases 的 SHA256 相同，产品代码独立冻结。不得将旧 v3 成绩与新 v3.1 结果作为产品改善比较，不覆盖旧批或自动重跑历史任务。本文中的冻结修订名不等于已创建付费执行 manifest。

本次关键文件 SHA256（真实批次仍须核对完整评测目录）：

| 文件 | SHA256 |
| --- | --- |
| `docs/agent-eval/benchmark-v2/cases.json` | `69a766951ed966d83693445e245e5467e6b82dde85a400ca16e4e6fb29fc3340` |
| `creatoros/evaluation/benchmark/grader.py` | `7c3b35907a421aa280def26ccc8948be019d29d6e53bff2ae96b7ac87ca7f9a2` |
| `creatoros/evaluation/benchmark/skill_evidence.py` | `fe228f79e0fd94a8ef94fdf0a527ac4796cd96659636eb4b13fcd192e8c8ec8e` |
| `creatoros/evaluation/benchmark/scoring.py` | `6539773a1f7b57265346673f9196dc304f6c84fb20ac05dfb58ea1cd4038293e` |
| `creatoros/evaluation/benchmark/manifest.py` | `ef150b3c2ffd7034390b8989dfbd575acd44ba983f6775ce01981d77dbbea971` |
| `creatoros/evaluation/benchmark/server.py` | `19fb1aac8a0f8efb00b0ad7ffdaaf8aa82af6b3b0aa72edf99c6331a5f70185f` |
| `creatoros/evaluation/benchmark/world.py` | `f20a66063bf8e3ebea3609b7eddfcffccea40cc34ac4ae6713aef02c011fd0cd` |
