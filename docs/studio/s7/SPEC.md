# Studio S7 SPEC — 真实演示闭环与交付

## 目标

将 S1–S6 收口为可以稳定演示的本地产品：单命令启动同源 Studio，从首次创建、选题 Preview/确认、后台生产到图片验收/批准的流程可执行、可刷新、可重启恢复。

## 本阶段边界

- 复用现有 FastAPI、React/Vite、ContentRun 状态机与 CodexProducer，不新增第二套工作流。
- 浏览器 E2E 使用隔离 SQLite/输出目录和受控生产器，只验证产品流程，不宣称为真实 Codex 生成。
- 另用临时账号/栏目做一次真实 Codex ContentRun 生图，不发布，不写正式运营库。
- 不做 S8/Agent Eval，不启动平台发布，不把未确认的个人内容放入 README。

## 预计改动

- `creatoros/web/`：托管 `web/dist` 并支持 React 详情路由直接刷新；启动前给出可行动的依赖/产物错误。
- `web/` 与 `tests/`：增加一条可重复的真浏览器 E2E，覆盖首次使用到返工/批准。
- `README.md`：改为一次安装、一个日常启动命令，补充 2–3 分钟演示步骤与真实边界。

## 验收

1. CLI 相关回归、Studio backend smoke、frontend typecheck/build 通过。
2. 单条命令启动后，`/`、`/creators/<id>`、`/runs/<id>` 同源返回 Studio，`/api/health` 仍返回 JSON。
3. 真浏览器 E2E 覆盖：空库创建 Creator/Series → Preview/确认 Topic → Run → 返工新版本 → 批准，并保留可诊断失败截图。
4. 真实 Codex 联调使用隔离目录，完成后用 `SocialContentPack.load()` 和 Studio Inspector 验收；若用量/登录态不可用，明确记录为延期。
5. 自查首页有数据、首次使用、生产中、图片 Inspector、失败/过期确认界面；不将本地路径、凭证或真实运营 trace 提交进仓库。

## 状态

- 2026-09-05：已完成。

## 实际实现

- `python -m creatoros.web` 现在会检查/构建 Vite 产物、升级数据库并在同一个本机端口托管 Studio 与 API；`Ctrl+C` 有序关闭不再暴露 traceback。
- FastAPI 同源托管 Vite assets，修正 Windows MIME；React 账号/Run 详情地址直接刷新可恢复，未知 `/api/*` 仍是 404 JSON，本机自定义端口可写、外部 Origin 仍拒绝。
- 顶部健康状态区分本地就绪、表单模式、Codex 未就绪和 API 连接失败；修正首次使用页面中过期的“下一阶段接入”文案。
- 新增 Playwright + 本机 Chrome E2E，使用每次新建的临时目录和明确标记的受控生产器；失败保留 trace/截图。
- 新增独立真实 ContentRun 脚本，可在中断后复用原 `producer_thread_id` 恢复，也可临时启动同源 Studio 查看产物。
- README 已收口到单命令启动、2–3 分钟演示路径、故障提示和真实联调边界；未经用户确认的真实生成图未加入 README。

## 验收记录

- 17 个 CLI/Storage/Operation/Run/Studio smoke 与 `compileall` 通过；`npm run typecheck` / `npm run build` 通过。
- `npm --prefix web run e2e` 使用真实 Chrome 通过（26.2 秒）：空库创建账号/栏目、Preview、另页先改队列造成过期确认、开始生产、图片 Inspector、返工第 2 版、批准、刷新恢复和 390px 无横向溢出。
- E2E 截图保留在已忽略的 `web/test-results/`；包含首次使用、过期确认、生产中、Inspector、有数据首页和手机批准态。
- 单命令在隔离数据库的 8881 端口实际启动；`/`、`/runs/restart-check`、`/api/health` 均返回 200，后者是 JSON；`Ctrl+C` 完成应用关闭且无 traceback。
- 真实 Codex S7 Run 首次生成了图片，但输出一份“制作中”的部分回执后未结束；主动中断后 Run 正确记为 `interrupted`。收紧 Prompt 后显式恢复同一 Run/thread，复用已有图片并完成 7 张真实轮播；重开数据库后状态为 `awaiting_approval`，7 个图片 URL 与 digest 全部通过。
- 恢复尝试 usage：`input_tokens=404789`、`cached_input_tokens=378752`、`output_tokens=5037`，缓存输入占比约 93.6%。真实 Inspector 已检查首页、中段与收尾卡，完整截图保留在已忽略的 `tmp/s7-final-qa/real-inspector.png`；没有批准或发布。
