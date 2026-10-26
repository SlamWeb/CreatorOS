# 制作任务隔离验收（2026-10-27）

## 真实模型链路

输入：“同一个人，从清晨到黄昏”保留为一条选题，拆清晨、黄昏两个制作任务。清晨使用栏目默认 Skill，黄昏指定同账号可访问的另一份 Skill，只交付两张图，不修改栏目绑定、不批准、不发布。

真实 React 页面发消息，DeepSeek 仅调用 `get_producer_skill`、`start_content_run` 各一次，没有重复轮询、没有新增 Topic。SQLite 最终 1 Topic、2 ProductionTask、2 Run、2 succeeded Attempt；每 Run 1 张图、状态 awaiting_approval。

| 任务 | 独立 Codex thread | 执行时间（UTC） | Codex 输入 / 缓存输入 / 输出 tokens |
| --- | --- | --- | --- |
| 清晨世界 | 01a1797c-5d8b-7573-b183-4e1b62d44fc9 | 18:32:30.691–18:33:20.353 | 65,076 / 42,368 / 1,741 |
| 黄昏世界 | 01a1797c-5cb2-75a1-9647-67805bc53617 | 18:32:30.873–18:33:25.625 | 86,041 / 63,488 / 1,269 |

上述时间从隔离数据库读取，两个区间重叠；缓存输入是输入的子集，不再相加。这里验证执行与交付，不把两张图当画风稳定性或角色一致性的质量评测。

页面在同一素材集中显示清晨、黄昏两张图片，并保持任务顺序。浏览器实际点击“下载所选 ZIP（2）”，压缩包包含 `0001.png`、`0002.png`、`manifest.json`。刷新前后 Run ID 不变，未再次生图。

## 没有抹去的失败

1. 首次隔离夹具的黄昏 Skill 未被当前账号引用，账号边界拒绝读取，未提交 Run。保留该轮对话；新建第二套夹具，将黄昏 Skill 绑定同账号的备用栏目后重验，没有放宽正式权限。
2. 第二轮 Playwright 在查询会话时出现一次 `socket hang up`，测试中止；后台两个 Codex 任务继续成功。随后使用同一个 session/Run 只读续验，通过前端任务列表、素材集和下载 ZIP 验证，没有重发 query 或生产。
3. 下载补验脚本曾试图在打开选题模态框时点击其背后的聊天关闭按钮，等待后终止；修正测试操作顺序，再只读续验。不是生产失败，也没有重生图。
4. 原素材集迁移回归曾使用新版 ORM 在 v17 老表上创建夹具，因老表无 task_id 失败。改为用历史表原有列播种；仍逐列核对原记录、nullable 新列、空新表、FK 与完整性，修后通过。
5. 旧上下文回归两处硬编码31个工具，当前基线实际暴露32个，先得到50/52。核对HEAD和工作区注册名称及账号allowlist一致，本次未新增工具；更新过时数量断言，保留跨请求schema完全一致等断言，再跑52/52通过。

## 可复现入口与原始证据

- 真模型服务器：`tests/live_production_tasks_server.py`，只允许新建在项目 tmp 内的目录，实际使用 DeepSeek/Codex。
- 真前端脚本：`web/playwright.production-tasks-live.config.ts` 与 `web/production-tasks-live/tasks.spec.ts`。`CREATOROS_TASKS_EXISTING_SESSION` 仅用于原任务只读续验，不再次发消息。
- 本地证据：`tmp/production-tasks-real-20261027-b/{conversation,accepted,completed,verification}.json`、`collected-images.zip`、`outputs/`。
- 原始首轮目录：`tmp/production-tasks-real-20261027`；浏览器失败/续验目录分别为 `web/tmp/production-tasks-live-results*`。这些运行证据未提交到仓库。
- 桌面真实收图截图：`web/tmp/production-tasks-live-results-b-export-final/tasks-real-frontend-DeepSe-b5f49-x-tasks-into-one-collection/real-collected.png`。

受控 HTTP/SQLite 回归用于确定性并发、失败注入、幂等、旧行保护；与上述真模型链路分开报告。正式服务、正式库、旧 RSI 和旧图片均未操作。
