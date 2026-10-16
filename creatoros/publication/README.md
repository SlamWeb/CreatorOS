# 小红书受控发布

运行环境：安装项目 `requirements.txt` 与 `requirements-web.txt`；启动已登录的小红书 MCP，再启动 Studio。MCP 是非官方浏览器自动化适配，不是小红书官方发布 API。

当前本机使用 `creatoros-xhs-login-probe` 容器。Docker Desktop 启动后：

```powershell
docker start creatoros-xhs-login-probe
python -m creatoros.web
```

内容页依次执行：验收 → 发布 → 准备发布单 → 检查账号/图片/文案 → 确认并发布。Agent 能准备预览和查询，不能替用户确认。发布后点击“刷新数据”；不做定时采集。

## 可配置项

| 环境变量 | 本机默认值 / 含义 |
|---|---|
| `CREATOROS_XHS_MCP_URL` | `http://127.0.0.1:18060/mcp`，仅允许本机 |
| `CREATOROS_XHS_MCP_TOKEN_FILE` | `tmp/xhs-mcp-smoke/auth-token.txt` |
| `CREATOROS_XHS_MCP_TOKEN` | 可选环境变量凭证，优先于凭证文件，勿提交 |
| `CREATOROS_XHS_STAGING_ROOT` | `tmp/xhs-mcp-smoke/docker-data/publications`，宿主图片暂存 |
| `CREATOROS_XHS_SERVER_STAGING_ROOT` | `/app/data/publications`，容器内同一挂载位置 |

Cookie 由 MCP 保管；发布单和账号绑定在 SQLite。已确认图片快照保留于 outputs 下 `.publication-snapshots`，与原产物分开。暂存目录只用于本次发布图片，不把整个项目挂载到容器。

## 结果不确定时

“发送完成”不等于已发布。系统查本人笔记核对作者、完整文案、图片数量及时间窗。上游没有返回可靠笔记 ID，平台也可能延迟展示；未找到唯一匹配时保持“需要核对”，不能直接重发。

可以点击“核对发布结果”，或粘贴 App 复制的完整 `xiaohongshu.com/explore/...` 链接。短链暂不支持。授权用的 xsec 参数仅在内存使用，不存入发布单或 Trace。图片数量核对不等于平台图片逐像素一致。

当前上游账号基本资料缺少稳定用户 ID，因此从本人已发布列表核实；完全没有笔记且无 ID 时拒绝发布，不拿昵称猜身份。分享/阅读等字段不可得时显示 `—`，不造零。

自动化测试仅在隔离库模拟不可逆发布，见 SPEC；真实发布仍需用户前端确认。
