# DeepSeek Skill 执行

## 2026-11-19 · 账号对话

账号 Agent 在当前账号的目录中发现 Skill，按任务读取具体版本的正文和参考文件。文案直接在对话交付。需要文件产物时，用户对本轮勾选「允许本轮运行脚本」，Agent 用同一个 `manage_skill_task` 接口准备任务、读写 UTF-8 文件、运行脚本和交付文件。

- 一次明确授权对应一个请求 ID。重放按已有对话请求恢复，不再启动一轮。普通消息、总览和计划自动续接没得到此授权时不暴露文件执行接口。与 RSI 模式互斥。
- 宿主按会话和请求确定目录，校验 Skill 的账号使用权及原文件摘要。Skill 与本轮图片、音频附件进入任务快照。相同选择可重读，修改已经准备好的输入需要发新消息。
- `skills/`、`inputs/` 只读，`work/`、`out/` 可写。Python 脚本在 Wasmtime 中执行，宿主只挂载本轮文件和可信运行库。执行环境不提供网络、宿主环境变量和子进程能力，默认运行限时 30 秒、内存上限 512 MiB、合计输出上限 32 KiB。运行器无法启动时返回原因。模型可按退出码和标准输出修正本任务的脚本。
- 交付显式调用 `publish`。宿主核验并保存独立的交付副本，随后在聊天显示下载入口。后续脚本修改工作文件时，已登记的交付文件仍按原摘要读取。任务与交付使用会话数据目录中的独立子目录。
- Skill 分类继续服务于查找。本模块复用已有 Skill 和业务权限，脚本是可选执行能力。四图拼接只作为通用读写和运行接口的验证场景。

验收在独立数据库、会话、输出和端口完成。文字用例确认真实 DeepSeek 按账号已登记的口播 Skill 写稿。文件用例验证模型实际创建脚本、执行脚本并交付四图合成，核对输出尺寸与四张原图像素。路径越界、未经授权的命令及跨会话下载在受控测试检查。

首个运行器支持 `.py` 和已配置的 WASI 依赖。交付单文件上限为 128 MiB，单批上限为 256 MiB。工作目录在脚本执行期间尚无磁盘配额。

## 隔离开发配置

脚本运行器是可选依赖。把下列配置放在本次启动的进程环境，文案工作流无需这些配置。

- Wasmtime Python 包固定为 `wasmtime==50.0.1`。放在应用解释器的依赖中，或将独立的包目录设置为 `CREATOROS_SKILL_TASK_WASI_PATH`。该目录可同时安装 `pypng==0.20220715.0` 供脚本读写 PNG。默认使用应用的 Python 解释器，可通过 `CREATOROS_SKILL_TASK_PYTHON` 指定可信解释器绝对路径。
- 将 `CREATOROS_SKILL_TASK_WASM_ROOT` 指向可信运行库目录，目录内包含 `python.wasm` 和 `lib/`。本次 Windows 验证使用上游 `python-3.12.0-wasi-sdk-20.0.tar.gz`，SHA-256 为 `6c1cddbb69ae09e87eee2906bdc70539bff5f2969818a6f8457d4e6a6eb67d4d`。将归档的 `bin/python-3.12.0.wasm` 放为 `python.wasm`，将 `usr/local/lib` 放为 `lib`。[CPython WASI 下载](https://github.com/vmware-labs/webassembly-language-runtimes/releases/tag/python%2F3.12.0%2B20231211-040d5a6)
- `python -m unittest tests.test_skill_task_runner` 在这些配置存在时实测隔离。真实端到端验收用 `python -m tests.live_deepseek_skill_tasks --live`，从进程环境读取 DeepSeek 凭证，并创建独立的临时 SQLite、会话和监听端口。
