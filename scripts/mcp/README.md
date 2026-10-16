# 小红书 MCP：仅连通与登录

这是独立学习探针，不是 CreatorOS 已上线的发布功能。不接正式数据库，不注册给账号 Agent，不发布、不点赞、不评论。先理解：`ClientSession` 负责 MCP 通信；小红书登录态由外部 Server 保管，两种 Session 不相同。

**当前验证状态（2026-10-16）：** 初始化、18 个工具发现、本机绑定与访问认证通过。Windows 防护拦截了上游启动浏览器所用的 `leakless.exe`，因此登录检查未完成，二维码命令未实际运行。不要为运行此探针关闭防护或设置排除项；先处理此安全兼容问题或审查替代部署。文件哈希一致只能确认与上游发布文件一致，不能证明安全。

本机后续已按用户授权重装 Docker Desktop 4.95.0：程序位于 `D:\DockerDesktop`，WSL 与容器磁盘位于 `D:\DockerData`，Linux Engine 29.8.2 的真实 version/info 检查通过。用户配置和日志仍可能留在 C 盘；这不是“整套 Docker 所有文件都迁出 C 盘”。旧数据保留于 `D:\DockerData-backup-20261016`，不要误当新引擎正在使用的目录。旧终端若找不到 credential helper，请重开终端加载新 PATH。Docker 引擎可用不代表小红书已登录，后续探针结果见 SPEC。

## 本机准备（Windows）

在仓库根目录创建 `tmp/xhs-mcp-smoke/`，下载上游 [v2.5.5 Windows MCP 二进制](https://github.com/xpzouying/xiaohongshu-mcp/releases/tag/v2.5.5)，保存为 `xiaohongshu-mcp-windows-amd64.exe`。启动脚本核验官方 release 的 SHA256，不运行未知版本。首次启动上游会下载独立浏览器；不是借用个人 Chrome 的登录态。

用项目解释器创建隔离环境，并限制包含凭证的目录权限：

```powershell
python -m venv tmp/xhs-mcp-smoke/venv
tmp/xhs-mcp-smoke/venv/Scripts/python.exe -m pip install -r scripts/mcp/requirements.txt
$taskUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
icacls tmp/xhs-mcp-smoke /inheritance:r /grant:r "${taskUser}:(OI)(CI)F" 'SYSTEM:(OI)(CI)F'
```

第一个终端启动服务，保留此进程；Ctrl+C 关闭服务，不删除登录态：

```powershell
tmp/xhs-mcp-smoke/venv/Scripts/python.exe scripts/mcp/start_xhs.py
```

第二个终端依次执行。`list` 只发现工具；`status` 只查登录；仅未登录且准备扫码时执行 `qr`：

```powershell
tmp/xhs-mcp-smoke/venv/Scripts/python.exe scripts/mcp/probe.py list
tmp/xhs-mcp-smoke/venv/Scripts/python.exe scripts/mcp/probe.py status
tmp/xhs-mcp-smoke/venv/Scripts/python.exe scripts/mcp/probe.py qr
```

打开 `tmp/xhs-mcp-smoke/login-qr.png`，本人用小红书 App 扫码并确认，再执行 `status`。二维码和会话凭证不分享、不提交；若过期，准备好后手动再取一次，不自动循环取码。遇到额外身份校验，由账号本人完成，不绕过。

## 安全与边界

- 服务只监听 `127.0.0.1:18060`，不开放公网。随机 Token、Cookie、日志均在忽略目录；不要把原始日志或该目录上传。目录 ACL 不是加密，也不防本用户权限下的恶意进程。
- `auth-token.txt` 只用于本机 Client 访问 MCP Server，不是小红书授权 Token；`cookies.json` 才是上游保存的网页登录态。启动时显式设置 COOKIES_PATH，避免捡到旧目录里的其他账号。
- Cookie 有效时可复用，但不能保证永久登录。连接成功、工具执行成功、账号登录成功分别核验，未扫码不能声称已登录。
- 当前探针固定 MCP SDK 1.30.0；使用该版本的 initialize/list_tools/call_tool，不混用其他协议版本示例。正式账号绑定、持续服务管理、二维码 UI、发布及结果核对全部暂不实现。
