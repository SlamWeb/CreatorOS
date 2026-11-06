# Tool calling v1 验证记录（2026-11-07）

## 定义与组件

- [cases.json](cases.json) 为父集 `creatoros-trace-benchmark-v3.4-20261105` 的 12 个完整原题，保留原 B 编号，实际定义、预期、证据及评分由父题直接复用；不是独立新增的 12 例。
- `python -m unittest tests.test_tool_calling_v1 tests.test_benchmark_suite_schema tests.test_benchmark_cases_v2 tests.test_benchmark_nonvideo_guard`：25 项通过。覆盖本套与父题字节结构等价、身份及选集、重复/越界拒绝、guard 白名单与既有协议；不是 12 题的真实模型成绩。
- `web` 执行 `npm ci` 和 `npm run build` 成功，为隔离浏览器测试构建本工作树的前端；未启动或修改正式服务。真实运行使用当前工作树锁文件安装依赖和实际构建结果。

## 隔离真实模型运行

两批分别冻结 B05/B06/B10，单 worker、真实浏览器、临时独立业务世界与隔离 SQLite，使用 `nonvideo-no-publication-v1`；模型和评估 Judge 均为真实 DeepSeek，详细配置、源码及前端产物逐文件哈希见对应 manifest。该守卫不限制全部网络访问，真实外部副作用不能靠 SQLite 事务回滚。没有运行看板或其他题。

| 批次 | 事实 | 原始结果 |
|---|---|---|
| `tool-v1-pilot-20261107-a` | 三题均在服务启动阶段失败，系统未配置 ffprobe，没有开始模型请求；语义判定 needs_review。 | `data/agent-benchmark-v2/tool-v1-pilot-20261107-a/`，失败和日志保留，**不覆盖**。 |
| `tool-v1-pilot-20261107-b` | 为进程配置现有 ffprobe/ffmpeg 后另开新批；三题全部执行完成，程序及自动语义均为 passed。 | `data/agent-benchmark-v2/tool-v1-pilot-20261107-b/`，包含冻结文件、每题记录及汇总。 |

使用的 ffprobe 版本 `n8.1.3-27-g8dca1586f4-20261031`，SHA-256 `b77db2dfcbf08886d6a29bc1fabdceea3dd559751b03087205e3e13ec85c3e15`；二进制由本机既有环境提供，不属于 benchmark manifest 的源码冻结成员。首次失败批与修复环境后的批次不能混作同条件比较。冻结时 Git HEAD 为 `e2f660756a55e31705a49839467b5997f238ea45`，工作树有未跟踪文件；manifest 保存 dirty 状态和实际运行相关源码/数据哈希，不能只凭 Git SHA 复刻。

下表 input/output/hit/miss 是 Agent 主请求的供应商 usage 累计，耗时为每题测试墙钟时间；Judge 请求和费用另在每题 `metrics.json` / `requests.json`，不能把表格当成本次全部消耗。服务端可能有既有缓存，首请求不能视为冷缓存。语义由现有 Judge 给出，未经过人工校准。

| 题 | Agent 请求 | input / output | hit / miss | 耗时 |
|---|---:|---:|---:|---:|
| B05 | 3 | 73,558 / 1,461 | 51,584 / 21,974 | 30.36 s |
| B06 | 2 | 42,394 / 428 | 40,704 / 1,690 | 16.75 s |
| B10 | 4 | 87,542 / 654 | 84,352 / 3,190 | 23.44 s |

全 DeepSeek（Agent + Judge）请求数依次为 4、3、5；输入分别为 100,507、50,540、99,804，输出分别为 1,799、503、875。原始 metrics 的美元费用是根据 manifest 冻结价格得到的**估算区间**，不是实际账单；Codex 订阅费用为未取得。没有因此推算真实平均成本或对比另一版本效果。

B01/B02/B03/B07/B18/B31/B32/B38/B40 本轮未作真实模型运行。尤其失败恢复、拒绝越权、提示注入和正向授权读取目前只有题目定义及既有组件/评分契约，不能声称本次专项实测通过。要比较其它版本必须使用新的 batch、同一冻结题集及尽可能相同的前端、模型、guard 和环境，保留全部失败与缺失值。

## 候选改动对照批 c

`tool-v1-pilot-20261107-c` 为新批次，不覆盖 b；同样选 B05/B06/B10、单 worker、同模型配置、相同守卫及 ffprobe/ffmpeg。冻结校验显示 b 和 c 的题集 SHA、题目选择、模型配置相同，413 个冻结文件中**仅** `creatoros/tools/model_projection.py` 内容不同；服务端缓存状态、网络和模型随机输出无法控制。冻结及逐题原始记录位于 `data/agent-benchmark-v2/tool-v1-pilot-20261107-c/`。

两个批次的三题均执行完成，自动程序判定和自动语义判定均为 passed；Judge 并非人工校准，不能将此等同人工验证。以下只列 Agent 主请求的实际供应商 usage，Judge 请求另见批次结果。累计输入是各请求输入之和，非最终上下文长度。

| 题 | 批次 | Agent 请求 | input / output | hit / miss | 耗时 |
|---|---|---:|---:|---:|---:|
| B05 | b | 3 | 73,558 / 1,461 | 51,584 / 21,974 | 30.36 s |
| B05 | c | 3 | 73,514 / 1,229 | 67,584 / 5,930 | 27.44 s |
| B06 | b | 2 | 42,394 / 428 | 40,704 / 1,690 | 16.75 s |
| B06 | c | 2 | 42,321 / 316 | 41,216 / 1,105 | 17.00 s |
| B10 | b | 4 | 87,542 / 654 | 84,352 / 3,190 | 23.44 s |
| B10 | c | 4 | 87,478 / 587 | 84,992 / 2,486 | 25.23 s |

每题仅一轮旧/新结果，命中差异尤其可能受既有服务端缓存与运行先后影响，不能把差异归因于代码，也不能据此推断稳定省钱或时延改善。这三题是自然业务回归而非专门针对 pending 回执去重的覆盖；该改动是否处理好各状态需看对应专门测试。

## 最终候选批 d

c 完成后，`model_projection.py` 又增加了对 `unknown` queue_state 和 observation_warning 的排除判断；因此 c 不是最终代码的测试。另行冻结并运行 `tool-v1-pilot-20261107-d`：与 b/c 相同的题集 SHA、三题、模型配置、guard、单 worker 和 ffprobe/ffmpeg 配置；冻结的 413 个文件中，相对于 b 或 c 均仅 `creatoros/tools/model_projection.py` 不同。外部缓存状态和网络不能固定。d 的三题均执行完成，程序和自动语义判定 passed，仍未经人工语义校准。

| 题 | Agent 请求 | input / output | hit / miss | 耗时 |
|---|---:|---:|---:|---:|
| B05 | 3 | 73,732 / 1,118 | 67,840 / 5,892 | 25.11 s |
| B06 | 2 | 42,371 / 380 | 41,216 / 1,155 | 17.72 s |
| B10 | 4 | 87,672 / 646 | 85,248 / 2,424 | 22.14 s |

计数为 Agent 主请求的供应商 usage 累计；所有 Agent + Judge 的请求数依次为 4、3、5，输入依次为 100,482、50,602、99,792，输出依次为 1,330、474、777。完整逐请求记录、估算费用及实验环境见 `data/agent-benchmark-v2/tool-v1-pilot-20261107-d/`；它不覆盖 b/c。三题每个版本只跑一次，命中、输入与时间差不能据此归因为代码优化或说明费用改善；这些题也未专门验证 unknown / observation_warning 排除语义。
