# v3评分标准与旧Eval退役验证（2026-11-02）

## 本轮交付

- 唯一题集仍为B01–B50；用户请求、来源、场景、40/10公开分组及嵌套约束投影与父版本SHA一致。版本为creatoros-trace-benchmark-v3-20261102。
- 50题逐题定义意图、预期结果、190条程序项、86条语义项。按任务决定条数，简单结果优先，复杂任务核对必要链路。
- 新执行记录保存独立program/semantic分数和关键项。题内二分类，集合按题平均；缺测留null并报告覆盖率，无综合权重。
- ZIP检查manifest顺序和实际成员SHA，核对原素材集或本次新Run；视频时长用真实浏览器媒体元数据。权限探针使用当前工具契约。
- 旧Eval运行/判分/题目规则与前端入口删除；历史日期报告移archive、data原证据不动。/eval只读当前50题及已保存批次，旧批无新scores就不补算。

## 已运行检查

- 108项评分、题目身份、冻结、汇总、采集、读取API、ZIP/视频元数据契约通过：tests.test_benchmark_report_v2、test_benchmark_scoring、test_benchmark_cases_v2、test_benchmark_manifest_v2、test_benchmark_store_v3、test_benchmark_media_oracles_v3、test_benchmark_metrics_v2、test_benchmark_grader_v2、test_benchmark_capture_v2、test_benchmark_server_v2。
- 独立子进程真实HTTP/SQLite权限探针通过；独立世界/收图/导ZIP检查及保留的两个产品回归通过。没有调用模型。
- TypeScript与隔离Vite构建通过；Chrome前端专项3/3（21.7秒）：逐个打开50题、两个分数与逐项依据、缺测/覆盖率、旧批不重算、证据失败重试与刷新；桌面1440×900、手机390×844截图已检查。
- 只读实际baseline-v21-001，旧42个联合通过与无scores保持不变；没有对旧记录调用汇总重写。

## 发现并纠正

- 旧单测锁死v2.1整个题目hash，与本轮修改评分矛盾；改为冻结全部50题的原请求/来源/场景/分组投影hash，不放松用户任务。
- 独立世界首次因未配置ffprobe不能初始化音频夹具；使用现有本地ffmpeg/ffprobe，不以Mock替代。
- 旧跨账号生产探针没有传现行action，实际先在Schema层失败；改为action=start与topic_ids列表，重新验证真实权限拒绝及同账号正对照。

- 收尾只读审查另补三项反例：浏览器刷新/Trace错误作为执行门禁，不因业务分覆盖而消失；缺SDK事件不当零次生图；只读查询次数上限仅作效率诊断，明确禁止重查和防重复写入仍按任务判断。

## 干净提交快照复核

- 用git暂存内容导出独立副本，未包含现有29个BGM/其他脏文件的改动。118项后端检查首轮117通过，唯一失败是SDK采集夹具缺少真实协议的turn/completed事件；补齐完整夹具并保留截断日志反例后，该项单独通过。
- 同一干净副本TypeScript和独立Vite构建通过；Chrome原3条专项再次3/3（20.5秒），没有使用正式dist。
- 生图计数的完整性依赖隔离SDK事件；终态回执本身不能替代事件日志，缺失按未评分处理。

## 边界

本轮只验证新规则、判分器和真实隔离前端接线，没有重跑付费50题、没有新DeepSeek/Codex成绩、没有人工Judge一致率或稳定性提升结论。正式服务未重启、正式dist未发布，旧任务、图片与分数未改。受控页面夹具与合成故障不是模型成绩。
