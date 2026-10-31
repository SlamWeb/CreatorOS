# Benchmark v2 implementation

用户要求、冻结/隔离/判分边界与验收见 `docs/agent-eval/benchmark-v2/SPEC.md`。
本目录仅承载新版本runner/fixtures/metrics/graders；不修改旧Eval报告、正式任务或模型生产逻辑。

2026-10-31已交付50题及两整轮真实前端rollout。最新冻结为`baseline-v21-001`/`8df3113`：程序断言46/50、联合Judge42/50另1待复核；旧轮35/50保留。真实SDK/SQL/媒体页证据、缺测和已证实的Judge误判分开记录，详见文档ROUND-002，不将联合分当人工真值。

78项组件在干净已推送Git副本通过；76项无模型/媒体依赖的契约接入CI，付费Live显式触发。独立库关闭后50/50完整性及逻辑落盘核对通过。测试服务已停止，正式服务未重启；两整轮255份产品/静态前端输入hash相同。
