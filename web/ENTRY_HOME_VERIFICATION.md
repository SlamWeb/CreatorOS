# CreatorOS 入口主页 · 2026-11-01

## 已确认与实现

用户选择：`/` 无 query 展示中文品牌入口，`/workspace` 进入原工作台；已有 `/?creator=...`、`/?series=...`、Run/Skill URL 直接打开原页面。保留当前蓝色 C 标和主色，参考用户给定 Mobbin 截图的大留白与大标题；无灰底次级按钮、无按钮 hover 下划线。

首页只读 `/api/creators?limit=4`，显示真实账号入口；不读取全量任务、不轮询、不调用模型。无账号与故障保留入口，读失败不编造账号。页面没有虚构客户、价格、评测数字或登录能力。

Layout 保持为同一个路由壳，工作台路径和旧 query 路径共用 Agent provider。主页不挂任务/Agent provider。灰底 secondary 改为白底蓝边；旧 quiet/text-link 和 shadcn link 的 hover 用颜色/浅蓝底反馈。正文普通链接规则保留。未顺带重写工作台账号树或设置布局。

## 验证

隔离临时 SQLite/文件目录、8877 服务及独立 dist；所有原业务数据保留。测试准备复用既有 calibration fixture，产物回归使用受控 Producer；409/503/空列表为受控故障/边界注入，不代表模型质量。

```powershell
npm --prefix web run typecheck
# 下列命令在 web 目录：
node node_modules/vite/bin/vite.js build --outDir D:/CreatorOS/tmp/entry-home-20261101/dist
$env:CREATOROS_PYTHON = 'D:/Anaconda4.7g/envs/deepcode/python.exe'
$env:CREATOROS_E2E_DIST = 'D:/CreatorOS/tmp/entry-home-20261101/dist'
node node_modules/@playwright/test/cli.js test e2e/home-page.spec.ts e2e/ui-foundation.spec.ts
```

- 合跑 3/3（38.4s）：首页 CTA/键盘/真实账号入口、刷新/返回、旧账号/栏目/Run 入口、无业务写入与原记录保护；空/失败/恢复；原产物返工、ZIP 下载、审批与持久化。
- 补用户 2550×1292 视口后主页专项 2/2（21.3s），与第一轮分开记录。截图包括 1440×900、390×844、2550×1292 与读取失败态，均已查看。既有账号较多时仍通过「查看工作台」访问完整列表；主页只展示前四个，不伪称最近排序。
- 独立 finish reviewer：`ship`，范围为本轮入口与确认的视觉约束，非全站审计。一次 detector `[]`。类型检查与隔离构建通过。
- 首次构建发现本地 Lucide 已无 Github 品牌图标导出，改为现有 CodeXml 源码图标；失败构建未部署。旧 >500kB 主包提醒仍在。
- 原来以 `/` 直接进入工作台的旧 E2E 需按新入口先点「进入工作台」或改为 `/workspace`；不声称全站旧套件全部通过，本轮用新增专项和原产物专项验证。

构建/截图/失败记录保留在忽略目录 `tmp/entry-home-20261101/`。本轮没有覆盖正式 dist、重启正式服务或改动后端；未触发生产/发布。
