# 安全检查与重启

避免“任务还在跑就重启”和“重启后误跑旧任务”。检查不代表授权重跑，重启也不重评旧 RSI。

## 怎么用

在项目目录、正常运行 CreatorOS 的 Python 环境里执行：

```powershell
python -m creatoros.maintenance check
```

只读检查聊天、生产、调研、提炼、讨论、校准、发布、反馈采集和已授权排队任务。暂停 RSI 还会核对实际试产凭证，不只看父任务状态。普通 `queued` Run 没有执行授权，不会因此开工。

- 返回码 0：本次未发现忙碌或无法核对的状态，不是持续空闲保证。
- 返回码 2：忙碌、元数据损坏、旧执行日志未核对，或服务不是可识别的新版本；不会强制关闭它。
- `check` 不建数据库、不迁移、不启动消费者、不修改任务。

普通重启：

```powershell
python -m creatoros.maintenance restart
```

默认只检查任务、备份数据库、正常重启并验证启动，不遍历旧图片/Trace、不导出整库逐行比对。旧 RSI、图片和分数原样保留，不恢复或重跑任务。

命令只控制已登记的本地实例；不支持维护协议的旧版服务仍需空闲后人工正常退出，不会按 PID 强杀。

自定义实例时，两条命令都需指定相同的真实路径和端口，例如：

```powershell
python -m creatoros.maintenance check --database-url sqlite:///tmp/my.db --output-root tmp/my-outputs --port 8877
```

## 重启的顺序

1. 再次确认空闲；活动任务、执行者持锁或无法读取的状态均拒绝。
2. 空闲后暂停接新操作，再检查一次；不先打断一个仍在工作的 Agent。
3. 备份 SQLite。
4. 请求旧服务正常退出；无杀进程或按名称扫描进程的后门。
5. 新服务先不开任务消费者；核对实例身份、数据目录和健康/数据库状态，通过后恢复接单。

## 迁移或恢复：完整校验

检测到数据库存在待执行迁移时，自动切换为完整校验。涉及数据/产物恢复，手动使用：

```powershell
python -m creatoros.maintenance restart --full
```

完整模式在重启前记录原行和文件 SHA256，启动后比对一次，不再在停机前重复比对。`--full` 只加强校验，不会替你恢复数据、恢复任务或重新生图。结果中的 `verification` 为 `startup`（普通）或 `full`（完整）；普通模式不报告未经检查的“全部文件一致”。

完整文件核对覆盖本地 Skill 数据、产物、会话、讨论、调研、选题参考原图及冻结输入。不是全仓库备份，不包括内置源码 Skill；文件保存摘要，SQLite 才保存完整备份。迁移可新增表/列并升级到代码的 head，但不能改变原字段值、旧标准/分数或图片。

## 出错后

证据位于数据库旁 `<数据库文件名>.maintenance/evidence/<本次编号>/`：

- `before.sqlite`：停机前数据库备份。
- `before.json`：普通模式仅记校验模式；完整模式保存原表/列/值和文件摘要。
- `verified.json`：消费者尚未启动时的核对结果。
- `result.json` / `failure.json`：成功或失败；同目录有启动日志。

新服务启动后核对失败会保持维护状态。先检查证据，不要重复重启、删旧状态或直接恢复消费者。停机前失败会尝试恢复旧服务接单，错误日志写不进也不会跳过。停机结果未知则不强杀、不再启动第二份；不自动回滚数据或重跑模型。

维护目录含本地 token 和数据库备份，`*.maintenance/` 已排除 Git，请勿分享。它不是对同一操作系统用户的安全隔离，也不声称 Windows chmod 等于私有 ACL。

## 验证

```powershell
python -m unittest tests.test_maintenance_state tests.test_maintenance_control tests.test_maintenance_cli tests.test_maintenance_process -v
```

用独立 SQLite/文件，磁盘满、损坏响应、忙碌标记为明确故障注入。进程测试真实启动临时端口 Studio、发维护请求并重启，核对旧 RSI/文件和未授权 Run；不调用模型，不触碰正式服务。
