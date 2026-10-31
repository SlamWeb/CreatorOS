# 运行 Trace Benchmark v2

题集见 [cases.json](cases.json)，方法见 [METHODOLOGY.md](METHODOLOGY.md)，历史扫描边界见 [TRACE_COVERAGE.md](TRACE_COVERAGE.md)。

从仓库根目录，用已安装产品依赖的Python执行。凭证从本地环境或忽略的`.env`加载；下面的`run`会真实消耗DeepSeek及Codex额度。

```powershell
python -m creatoros.evaluation.benchmark.run freeze --batch pilot-001 --cases B01
python -m creatoros.evaluation.benchmark.run run --batch pilot-001 --workers 1
python -m creatoros.evaluation.benchmark.run freeze --batch baseline-001
python -m creatoros.evaluation.benchmark.run run --batch baseline-001 --workers 3
python -m creatoros.evaluation.benchmark.run report --batch baseline-001
```

每批产出在`data/agent-benchmark-v2/<batch>/`，目录被Git忽略。`manifest.json`及源码ZIP锁定运行输入，`RESULTS.md`与`summary.json`汇总固定分母，各`records/<run>/`保留浏览器截图、query/最终回答、SQL前后记录、工具参数与结果、真实模型请求和费用估算。

Codex桌面中优先打开`RESULTS-LOCAL.md`：它用本机绝对路径链接逐题证据。`RESULTS.md`保留相对路径用于整体目录移动；两份展示同一组分数。

同批同题不重跑、不择优。源码变化时停止该冻结版本；修复后新建批次。旧批不覆盖。业务结果、轨迹和语义Judge分开展示，缺证据为待复核，不能算通过。

隔离依靠每题独立磁盘库与目录，不依靠异步任务外层事务回滚。结束后关掉本题进程并封存隔离库和产物以便复查；不会回滚外部模型费用或删除SDK自己的会话日志。正式服务和旧任务不参与启动、重启或清理。

当前结果见 [ROUND-002.md](ROUND-002.md)，首轮原始失败见 [ROUND-001.md](ROUND-001.md)。各批保存自己的`frozen-cases.json`，新版定义不会用于重算旧批。`isolation.json`记录本题服务已停止，隔离世界留作复查。

自动CI只跑76项无模型/媒体依赖的契约测试，不偷偷消耗API或Codex额度；本机另跑2项真实HTTP/媒体夹具，共78项。真实模型的50题是单独的Live benchmark，报告同时列程序断言、未人工校准的语义Judge与联合结果，三者不能互称。
