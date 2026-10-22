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

需要重启时再明确执行：

```powershell
python -m creatoros.maintenance restart
```

本轮开发没有对正式服务执行重启。升级前已启动的旧服务没有维护协议，命令会拒绝自动接管。第一次需等任务结束，人工正常退出旧服务，再用平时的 `python -m creatoros.web` 启动新版；之后才能使用维护命令。

自定义实例时，两条命令都需指定相同的真实路径和端口，例如：

```powershell
python -m creatoros.maintenance check --database-url sqlite:///tmp/my.db --output-root tmp/my-outputs --port 8877
```

## 重启的顺序

1. 再次确认空闲；活动任务、执行者持锁或无法读取的状态均拒绝。
2. 空闲后暂停接新操作，再检查一次；不先打断一个仍在工作的 Agent。
3. 备份 SQLite，记录原行每个字段和受管文件 SHA256。
4. 请求旧服务正常退出；无杀进程或按名称扫描进程的后门。
5. 新服务先不开任务消费者、不恢复旧任务；健康、迁移版本、数据库完整性、旧行及文件全部核对后，才恢复正常工作。

文件核对覆盖本地 Skill 数据、产物、会话、讨论、调研、选题参考原图及冻结输入。不是全仓库备份，不包括内置源码 Skill；文件保存摘要，SQLite 才保存完整备份。迁移可新增表/列并升级到代码的 head，但不能改变原字段值、旧标准/分数或图片。

## 出错后

证据位于数据库旁 `<数据库文件名>.maintenance/evidence/<本次编号>/`：

- `before.sqlite`：停机前数据库备份。
- `before.json`：原表/列/值和文件摘要。
- `verified.json`：消费者尚未启动时的核对结果。
- `result.json` / `failure.json`：成功或失败；同目录有启动日志。

新服务启动后核对失败会保持维护状态。先检查证据，不要重复重启、删旧状态或直接恢复消费者。停机前失败会尝试恢复旧服务接单，错误日志写不进也不会跳过。停机结果未知则不强杀、不再启动第二份；不自动回滚数据或重跑模型。

维护目录含本地 token 和数据库备份，`*.maintenance/` 已排除 Git，请勿分享。它不是对同一操作系统用户的安全隔离，也不声称 Windows chmod 等于私有 ACL。

## 验证

```powershell
python -m unittest tests.test_maintenance_state tests.test_maintenance_control tests.test_maintenance_cli tests.test_maintenance_process -v
```

用独立 SQLite/文件，磁盘满、损坏响应、忙碌标记为明确故障注入。进程测试真实启动临时端口 Studio、发维护请求并重启，核对旧 RSI/文件和未授权 Run；不调用模型，不触碰正式服务。
