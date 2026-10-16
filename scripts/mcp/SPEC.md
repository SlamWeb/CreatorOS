# 小红书 MCP 登录探针

## 2026-10-16：连通已验证，登录阻塞

- 仅验证真实 MCP 初始化、工具发现、登录检查和人工扫码。暂不接 Agent Registry、正式账号数据或发布；不调用点赞、评论、删除登录态等工具。
- 独立 Python venv 与上游二进制放在 Git 忽略的 `tmp/xhs-mcp-smoke/`，不修改生产 requirements。Windows 目录 ACL 限当前用户及 SYSTEM；服务仅监听 127.0.0.1:18060，并使用随机认证 Token。
- 固定上游 xiaohongshu-mcp v2.5.5，Windows exe 的 GitHub release SHA256 为 `cb55674f90c1649c875be9c4cde3af193b02fc34ffc860c40c8d711e6ca53e27`。启动前核验；不读取用户已有浏览器或其他 Cookie 文件，显式指定本探针目录的 cookies.json。
- MCP Client 用官方 Python SDK 1.30.0，通过 Streamable HTTP initialize/list_tools/call_tool。探针只有 list/status/qr 三个选项；每次执行有总超时。二维码写本地 PNG，不打印 Base64 或凭证。
- MCP 已连通和平台已登录分别报告；扫码由用户本人完成。只在用户请求登录时取二维码，过期不自动重复生成。登录态有效时复用；不能保证永久有效。
- 验收：实际绑定地址和认证、工具列表、未登录状态、二维码可读取、用户扫码后再次 status。若尚未扫码，明确记录未验证登录成功。

### 本轮实际结果

- 上游 release exe SHA256 与 GitHub asset digest 一致；隔离 venv 安装 MCP 1.30.0，生产依赖未修改。上游首次下载浏览器至 `%LOCALAPPDATA%/xiaohongshu-mcp/browser/148.0.7778.215/`，非项目 Cookie 目录；未连接个人浏览器。
- 真实绑定 `127.0.0.1:18060`，无认证请求返回 401；SDK initialize 协商为 `2025-11-25`，tools/list 返回 18 个工具，连通验证通过。
- `check_login_status` 实际返回 isError：上游启动浏览器的 `leakless.exe` 被 Windows 拒绝，错误明确表示病毒或潜在不受欢迎的软件。不能据此确认恶意，也不能擅自认定误报；没有关闭防护、添加排除项或重复尝试执行被拦截文件。
- 停止本次 probe 服务；没有调用二维码/发布/点赞/评论，没有扫码、没有验证账号登录成功。二维码命令仅完成代码编译检查，尚未运行。保留本地错误日志，未提交任何 Cookie/Token/截图/第三方二进制。
- Docker CLI 已安装，但 `docker version` 无法连接 Docker Desktop Linux engine（命名管道不存在），本轮未启动 Docker Desktop 或拉取镜像。下一步需选择并审查其他部署方式，不可将本轮称为登录验收完成。Python py_compile 与 git diff --check 通过，无模型 API 调用。
