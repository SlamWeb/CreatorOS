# 工作台账号导航与概览 · 2026-11-02

## 已确认的范围

用户选择账号切换器、只显示当前账号栏目、统一创建入口；账号概览先显示栏目入口和近期作品。保留 Logo、主题、后端和现有 URL，不实施栏目设置侧面板、全站 Tabs 或素材列表重设计。

## 实现

- `AccountNavigator` 负责搜索/切换账号、当前栏目列表和新建触发。URL 仍是所选身份来源，不另建一份账号选择存储；未分配栏目仍有入口。栏目超过 7 个时显示栏目搜索。
- `WorkspaceDialog` 使用原生 dialog 的焦点约束和顶层显示，创建表单不参与目录滚动。栏目表单明确归属，失败保留输入，取消/Escape 返回触发按钮；提交期间禁用重复提交与关闭。新账号从切换器创建，成功后定位新账号；新栏目成功后定位新栏目。
- `AccountOverview` 显示本账号全部可见栏目；原先逐栏目铺开 TopicLibrary 的概览分支移除。近期作品仅使用现有 `/api/runs?limit=50&offset=0&creator_id=…`，不遍历栏目请求详情。过滤可见栏目及有封面的 Run，按更新时间降序，同选题保留最近一件，最多 6 件；只代表最近 50 条执行记录中的有封面作品，不保证展示全部历史或无封面视频。
- 产物链接携带账号概览返回地址；保留 Layout 层聊天宿主、账号范围、旧产物和任务。未更改共享主题变量或 Logo；替换旧 rail CSS 并归并工作台响应式规则。保留聊天悬浮按钮样式，避免移除旧侧栏样式时误伤它。

## 构建与隔离

工作区有未完成的 BGM/聊天/后端改动。本轮未暂存或发布它们：以前端 HEAD 快照叠加本轮目标文件，在 `tmp/account-navigator-20261102/source/web` 独立构建。提交中的 WorkspacePage 与构建输入一致；工作区 BGM 差异继续保留。

类型检查（当前工作区与独立前端快照）通过。正式候选 `tmp/account-navigator-20261102/dist-final/` 构建通过；既有主 JS 包超过500kB提示保留，不把代码分包扩大进本轮。

## 行为验证

隔离临时 SQLite/文件目录，服务8877，未操作正式8765数据。正常创建与读取走真实隔离API；仅409为故障注入。已有 E2EProducer 生成本地可解码PNG，不调用 DeepSeek/Codex，不证明模型质量或真实生图能力。

最终 5/5，32.1 秒：

1. 搜索账号、切换后只显示当前栏目、URL刷新与浏览器返回。
2. 新栏目归属与持久化、成功定位；409保留草稿且不跳转，取消/Escape恢复焦点，不自动重发。
3. 新账号持久化、定位与空态；390px切换器搜索和新建入口可见、对话框不越界。
4. 7件隔离有图产物与1条未生产草稿：最多6件近期作品、逐张真实解码、准确产物往返；聊天保持同一账号，零聊天发送；桌面和390px布局。
5. 已有栏目管理回归：空栏目仍能管理、Skill详情读到完整内容、关闭恢复焦点、主导航滚动固定，390px/320px不横向溢出。更新旧概览数量断言和带版本号的按钮定位，未改变产品规则迁就断言。

执行（web目录）：

```powershell
$env:CREATOROS_PYTHON='D:/Anaconda4.7g/envs/deepcode/python.exe'
$env:CREATOROS_E2E_DIST='D:/CreatorOS/tmp/account-navigator-20261102/dist-final'
node node_modules/@playwright/test/cli.js test e2e/account-navigator.spec.ts e2e/workspace-management.spec.ts --grep 'searchable account|creating a column|creating an account|account overview lists|workspace tree keeps' --output=D:/CreatorOS/tmp/account-navigator-20261102/results-final
```

不是全站套件全通过。测试过程保留：首轮脚本将加载态误判为手机折叠态；第二轮3/4时旧夹具缺Skill；第三轮3/5时夹具Skill缺image声明及旧按钮定位遗漏v1。修的是等待条件/夹具/断言，没有修改后端。记录分别位于 `results/`、`results-r2/`、`results-r3/`，没有覆盖旧失败。

## 视觉与限制

已检查桌面空账号、390px账号切换器、最终桌面作品概览与390px概览截图。图片为受控PNG，不能代表真实作品的审美效果；全页截图在滚动后的sticky位置不用于宣称初始首屏位置正确。未建立截图差异基线、未做全键盘/屏幕阅读器完整审计。搜索及创建支持原生焦点/Escape并有实际断言。

本轮一次静态 Detector 扫描4个目标TSX返回 `[]`；它不证明交互或跨页面布局正确。

## 静态发布

已将验证dist的新静态资源补入正式目录，再原子替换入口；旧资源和回退入口保留。HTTP首页/JS/CSS与被测构建一致，health 200；后端PID发布前后均15436。未重启后端、未修改正式数据库、未触发生产或重跑旧任务。收据与回退入口位于 `tmp/account-navigator-20261102/deploy/`。
