# Tailwind / shadcn 首批迁移验证 · 2026-11-01

## 范围

- 接入 Tailwind 4.3.3、Vite 插件、shadcn 的 Button/Input/Textarea 本地源码及 `@/` 别名。
- 迁移产物页常用动作/返工与人工发布表单、运行控制、图片下载、公共错误重试。
- 使用原 theme.css，保留布局、Logo、配色、后端 API 和业务状态。不启用全局 Preflight；尚未迁移的控件继续使用原 CSS。
- Typecheck 改为 `tsc -b --pretty false`，实际检查项目引用中的应用和 Vite 配置。

## 验证方式

隔离 SQLite、输出目录与 8877/8878 端口，不使用正式 8765、正式库或正式 dist。
测试数据通过既有 calibration fixture 创建，再在隔离服务中使用受控 E2EProducer；不调用模型，不把纯色测试图当生成质量证据。
返工 409 与服务读取 503 为明确的故障注入，其余操作走真实 HTTP/SQLite/下载。

```powershell
$env:CREATOROS_PYTHON = 'D:/Anaconda4.7g/envs/deepcode/python.exe'
$env:CREATOROS_E2E_DIST = 'D:/CreatorOS/tmp/ui-foundation-20261101/final-dist'
npm --prefix web run typecheck
# 在 web 目录运行：
node node_modules/vite/bin/vite.js build --outDir D:/CreatorOS/tmp/ui-foundation-20261101/final-dist
node node_modules/@playwright/test/cli.js test e2e/ui-foundation.spec.ts
```

## 结果与限制

- 迁移前 TypeScript/构建通过；baseline-results-v6 的专项操作 1/1（10.5 秒），留下桌面/手机截图。
- 首次迁移的构建发现机械替换产生无效 CSS 选择器，修正后构建通过；未部署失败构建。
- migrated-results-v2 操作 1/1（26.2 秒），覆盖返工内容、409 保留草稿并阻止批准、重新检查、删除确认/取消、下载 ZIP、刷新、返回与 503 重新连接。
- 截图对照发现删除触发器的图标被旧选择器染灰，修复作用域并补计算颜色断言；最终确认见 web/SPEC.md。
- 最终 final-results-v2：专项 1/1（25.1 秒），以上操作加上真实保存返工、确认新版本尚无 attempt、显式启动受控生产、批准第二版并刷新；读取 API 证实旧三张图仍在、指令准确和已批准状态持久化。修正后桌面/手机两张图已查看，危险动作保持红色。
- 早期测试准备失败（旧隐藏添加入口、旧生产按钮、导入产物没有返工权限、不支持的合成双 Skill、指定版本缺少 request_id），均保留在 baseline-results/v2–v5。final-results 的后置检查错误地在批准后寻找返工，按现有权限修正测试顺序；没有改后端规则来迁就测试。
- 已查看 1440×900、390×844 的前后产物页截图，布局保持，无横向溢出；不是全站视觉回归。
- Impeccable 检测 `[]`；不是模型能力、发布平台或全站测试。正式服务未重启，正式 dist 未覆盖。
- npm audit 的两项 high（既有 Vite 8.2.2、source-map-js 1.2.1）在原锁文件中已存在且版本未变化；本次不做无关依赖升级。既有主包 >500kB 提示保留。

本地构建、失败 Trace 和截图保留在忽略目录 `tmp/ui-foundation-20261101/`，不提交测试数据库或素材。
