# 运行当前50题 Agent Benchmark

唯一现行题目为 [cases.json](cases.json)（creatoros-trace-benchmark-v3.3-20261105），逐题意图、预期结果和程序/语义规则见 [CASE_STANDARDS.md](CASE_STANDARDS.md)，现行契约见 [SPEC](SPEC.md)。

每题有自己的程序检查和语义问题，不统一凑条数。两类分数分开；集合按题分别取均值，暂不设综合权重。未知结果留null并报覆盖率，关键错误独立阻止通过。工具Schema、Token/缓存/延迟和成本单列诊断。

## 真实运行（会消耗额度）

从仓库根目录，用已安装产品依赖的Python执行；凭证从环境或忽略的.env读取。

```powershell
python -m creatoros.evaluation.benchmark.run freeze --batch oracle-v33-pilot --cases B12
python -m creatoros.evaluation.benchmark.run run --batch oracle-v33-pilot --workers 1
python -m creatoros.evaluation.benchmark.run freeze --batch oracle-v33-baseline
python -m creatoros.evaluation.benchmark.run run --batch oracle-v33-baseline --workers 3
```

本轮冻结身份 `oracle-v33-calibration-20261105`；前后产品对比须使用同一份修正题集与判分/采证源码，各建新批次。旧批不改写、不重判，当前没有本修订的50题真实模型成绩。

运行前先冻结全部定义、所选题、产品/判分源码与配置。每批每题仅首尝试，不择优；变化后另建批次。数据在data/agent-benchmark-v2/<batch>/；summary.json和RESULTS.md/RESULTS-LOCAL.md含逐题分数、原始证据入口及成本。没有重复独立trial时不计算pass^k。

每题独立进程、磁盘SQLite、会话和文件目录，从真实前端发送；真实DeepSeek，必要时真实Codex。测试世界封存供审计，不靠生产库外层事务回滚；费用和SDK全局日志不能回滚。正式服务不参与启动、重启或清理。

## 查看与回归

- /eval只读当前50题和已保存批次；新协议每题可展开程序/语义逐项依据。历史批按自己的冻结定义和原分展示，不自动重判；旧批未保存新分数时明确缺失。
- 组件回归：python -m unittest tests.test_benchmark_b12_title_matching tests.test_benchmark_oracle_revision tests.test_benchmark_cases_v2 tests.test_benchmark_scoring tests.test_benchmark_media_oracles_v3 tests.test_benchmark_manifest_v2 tests.test_benchmark_report_v2 tests.test_benchmark_store_v3
- CI只做无付费模型的组件/HTTP/受控浏览器检查；真实50题Live另行显式运行。
- v3.1 判分/采证验证见 [VERIFICATION-20261103-ORACLES.md](VERIFICATION-20261103-ORACLES.md)；原 v3 验证仍见 [VERIFICATION-20261102.md](VERIFICATION-20261102.md)。没有按新标准运行的成绩，不冒称完成新基线。

历史真实批次见ROUND-001/002/003-FOUR；旧12/20题规范已退役，历史文档在../archive，不是现行标准。方法来源见METHODOLOGY.md，Trace扫描边界见TRACE_COVERAGE.md。
