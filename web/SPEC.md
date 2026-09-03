# CreatorOS Studio Web SPEC

## 当前理解

- React + TypeScript + Vite 是本地 Studio 的展示层；Python/FastAPI 仍是业务查询与状态真相。
- S2 只读消费 `/api/overview`、`/api/creators`、`/api/series/*`、`/api/runs`，不直接连接 SQLite，也不调用 LLM、PersonClone 或 Codex。
- 产品主入口是“今天 / 账号 / 运行”。栏目和选题在账号详情、栏目详情中展开，避免首页铺满历史队列。

## 本轮目标（S2）

- 建立可运行的 Web Studio 外壳：响应式侧栏、真实 API client、React Router 页面和 TanStack Query 服务器状态。
- 首页、账号目录、账号详情、栏目选题、运行列表/只读详情都能用真实 DTO 渲染；空库、加载和 API 错误都有清晰下一步。
- 先把“看见现在有什么、下一步能做什么”做对；创建、调序、生产、图片验收、批准留给 S3–S5。

## 设计决策

- S2 采用一份轻量 `styles.css`，而不是现在就搭 Tailwind/shadcn 设计系统：页面数量少、视觉规则集中，先控制依赖和可读性；出现重复组件后再抽象。
- Vite 开发服务器把 `/api` 代理到 `127.0.0.1:8765`，因此浏览器不会接触数据库或处理跨域写权限；`VITE_API_BASE_URL` 可用于同源部署以外的只读地址。
- 组件只展示后端给出的状态/允许动作，按钮在未接通写接口时明确禁用并标注阶段；不写入演示账号，不把空库填成假数据。
- 首屏不放固定 KPI、巨大 ASCII Logo 或霓虹渐变；数字均来自 `OverviewView.counts`，状态同时显示文字和低饱和色点。

## 对外影响

- 新增 `web/` 前端工程与 `web/package-lock.json`；生产构建产物和 `node_modules` 忽略。
- README 增加双终端启动方式：FastAPI 在 8765，Vite 在 5173。
- 后端 S1 API 不改写、不新增写路由；S2 只验证它的真实读路径。

## 验收

- `npm run typecheck`、`npm run build` 通过。
- 正式空数据库显示首次使用引导，不显示虚假 Creator；服务不可达显示错误与重试。
- 以隔离临时 SQLite 启动同一 API，页面能显示真实测试账号/栏目/选题布局；测试数据不进入正式库。
- 在 1440×900 和 390px 宽检查首屏层级、无横向溢出、详情可回退；浏览器刷新详情 URL 仍由前端路由承接。

## 最近验证

- 2026-09-03：`npm install` 安装 31 个前端依赖，`npm run typecheck` 与 `npm run build` 通过（Vite 8.2.2，产物约 284 kB JS / 13.4 kB CSS）。
- 2026-09-03：启动真实 `python -m creatoros.web` + Vite，浏览器访问 `/`；正式空库显示首次使用引导，未出现假账号。
- 2026-09-03：以 `tmp/studio-demo.db` 隔离 SQLite 启动同一 API，浏览器检查首页、账号目录、账号详情、栏目选题详情；真实 Creator/Series/Topic 投影可见，页面 DOM `scrollWidth === innerWidth`（415px 移动视口），无控制台 warning/error。
- 2026-09-03：S2 未调用 LLM、PersonClone、Codex、生图或发布 API；临时演示数据未进入 `data/creatoros.db`。

## 下一步

- S3：接账号/栏目/选题创建与统一 Preview → 确认服务；前端写操作仍需服务端版本保护。
