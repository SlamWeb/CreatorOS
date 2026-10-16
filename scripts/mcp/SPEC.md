# 小红书 MCP 登录探针

## 2026-10-16：Docker MCP 与二维码验证通过，等待本人扫码

- 固定镜像 `xpzouying/xiaohongshu-mcp:v2.5.5` 拉取成功，实际 digest 为 `sha256:5bc690d0c7907f069886ddeb6b37abbe4bdd37bd431975b914c7c27435b1b602`，按此 digest 启动容器 `creatoros-xhs-login-probe`。
- 仅映射 `127.0.0.1:18060`，未开公网；AUTH_TOKEN 从本地忽略文件读入环境，不打印、不写 Git。仅挂载 `tmp/xhs-mcp-smoke/docker-data` 到 `/app/data`，Cookie 路径显式固定，不读取个人浏览器或旧 Cookie；未挂载 Docker socket。容器无自动重启策略，不做自动发布。
- 真实验收：initialize 协商 2025-11-25，tools/list 18 个；无认证 HTTP 401；check_login_status 成功返回“未登录”（非工具错误）；get_login_qrcode 成功保存 PNG 于忽略目录 `tmp/xhs-mcp-smoke/login-qr.png`，已展示给用户。
- 仍未验证扫码后的登录成功，不调用发布/互动；等待账号本人扫码后仅再次 status。二维码与本地 Cookie/Token/原始日志不提交。Docker 成功绕开本次 Windows 原生启动失败路径，不意味着原生文件已被证明安全或所有后续调用均可靠。

## 2026-10-16：用户授权卸载并迁往 D 盘（引擎验证通过）

- 用户明确允许恢复出厂，并进一步要求卸载重装、程序及大体积数据放 D 盘。先通过已验证 Docker Inc 有效签名的原安装器卸载，退出码 0；不操作其他 WSL 发行版或 CreatorOS 数据。
- 检查发现旧程序在用户 C 盘，原 WSL 数据位置其实已为 `D:\DockerData`，残留 `docker_data.vhdx` 为 16,032,727,040 字节。为保留恢复可能，重装前将旧数据目录同盘改名为 `D:\DockerData-backup-20261016`，未永久删除。
- 原 Local Docker、Local docker-secrets-engine、Roaming Docker 目录同父目录改名为 `*-reset-backup-20261016`，以隔离失效 socket 与旧配置。旧程序卸载器少量残件的进一步删除被工具策略拒绝，未绕过。
- 官方 4.95.0 安装包下载至 `D:\DockerInstallers`；计划程序路径 `D:\DockerDesktop`、新 WSL 数据 `D:\DockerData`，二者不嵌套。安装器官方 SHA256：`73431db38491f770543880366228a23c993f84d1e8e045f56d83b8a98940cf76`。须待完整哈希与数字签名核验后执行，不将下载中或安装成功等同于引擎可用。
- 官方安装参数支持 installation-dir 与 wsl-default-data-root；用户级日志/配置可能仍保留在 C 盘，不能承诺所有字节都离开 C 盘。尚未恢复小红书登录测试。
- 完整安装包 SHA256 与官方一致，Authenticode 为 Valid / Docker Inc；以 `--user --installation-dir=D:\DockerDesktop --wsl-default-data-root=D:\DockerData --backend=wsl-2` 安装，退出码 0。安装临时目录也指定为 D 盘，不改系统全局 TEMP。
- 注册表 InstallLocation 确认为 `D:\DockerDesktop`、版本 4.95.0；用户 PATH 已更新为其 resources/bin。新 WSL 注册位置为 `D:\DockerData\main`，容器数据盘为 `D:\DockerData\disk\docker_data.vhdx`。真实 `docker version/info` 返回 Linux Engine 29.8.2，初始 0 容器 / 0 镜像；旧 socket 崩溃本轮未复现。
- 旧终端进程仍可能缓存 C 盘 PATH；新开终端即可，当前验证显式加入 D 盘 resources/bin（包括 credential helper）。开始拉取固定 `xpzouying/xiaohongshu-mcp:v2.5.5`；拉取成功与登录成功仍需分别验收。

## 2026-10-16：Docker 替代部署尝试（未连通）

- 用户授权尝试 Docker；执行 `docker desktop start`，未拉取镜像、未创建容器、未调用发布工具。
- Docker Desktop 4.84.0 启动日志明确记录 backend crashed：初始化 Inference manager 时无法访问/移除 `%LOCALAPPDATA%/Docker/run/dockerInference`，引擎随即关闭。此时不是小红书登录失败，而是容器运行环境尚未启动。
- 只读检查发现该对象为 0 字节 ReparsePoint，最后修改时间为 2026-07-29。残留 socket 是候选解释，尚未确认其具体锁持有者或损坏原因。Docker 官方问题库存在同类报告：https://github.com/docker/desktop-feedback/issues/625 。
- 未恢复出厂设置、删除 Docker 数据、关闭防护、修改系统配置或重启电脑。下一步先由用户决定是否修复本机 Docker；引擎可用后才继续固定镜像版本、仅本机映射端口、挂载独立 Cookie 目录并验证认证/list/status/qr。
- 本轮不声称 Docker 部署或扫码成功；原 Windows 探针结果与生产数据保持不变。

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
