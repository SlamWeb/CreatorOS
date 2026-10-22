# CreatorOS 明确发给 Codex 的完整输入包

这里的“完整”指 CreatorOS 能管理的 SDK 输入，不包括 Codex 服务内部不可见的 System Prompt。样本来自 2026-10-22 已完成的隔离 RSI 第1轮，不是为本图重新调用生产。

## 1. Developer 原文

```text
本次是独立内容生产。仅使用当前请求、当前阶段 Skill、其资源及本次明确提供的内容。不要读取全局记忆、其他任务的会话/产物或项目开发资料。
```

对应 `creatoros/integrations/codex.py` 的 `PRODUCTION_RULES`，与实际 TextInput 开头重复属于当前代码事实。

## 2. 创建线程和模型配置

```text
model = gpt-6-luna
effort = xhigh
approval_mode = deny_all
sandbox = workspace_write
cwd = <本次 Attempt>/work
memories.use_memories = false
memories.generate_memories = false
project_doc_max_bytes = 0
```

这是目前项目配置，不是声称 SDK 默认值永远如此。新 RSI 轮次 `thread_start`，不传旧 thread ID；技术恢复才 `thread_resume` 精确的同一轮线程。禁用记忆和项目文档不是对所有外部文件/工具的万能安全屏障，账号归属仍由 CreatorOS 工具层校验。

## 3. thread.turn 的输入列表

```text
TextInput(text = production_instructions.txt 的完整正文)
SkillInput(name = iteration-live-vocabulary,
           path = <本次 Attempt>/skills/single/SKILL.md)
LocalImageInput(path = <本次 Attempt>/references/reference-01.jpg)
```

`<本次 Attempt>` 对这个真实样本是：

```text
D:\CreatorOS\tmp\rsi-chat-recovery-live-20261022\studio-producer-skills\extractions\jobs\4ea1c7bb1d4f66cf5f879d4593a9d12dad8e8da0c0b99227330fbd84ac15590e\trials\58c2388530353bce\revision-001\attempt-001
```

[完整 TextInput](codex-production-prompt.txt)保留原始绝对路径和完整选题/用户要求，不做省略号伪装成“完整”。原文件 SHA256：`97fa2a8675eaa6262c6acf0c8167975adda76156f6ff9938df74a64c12fb3fe1`。

Skill 原件和参考图片没有上传到本文档目录：它们仍在隔离证据目录，不把实际像素变成长 base64 文本。路径能帮助本机回查，不能在其他机器上自动获得那些文件。

## 4. 双 Skill 与恢复时有什么不同？

Mind + Visualize 会冻结两份目录，并在同一 Codex thread 中先做一次组合适配检查；检查 Prompt 另存 `composition_request.txt`，不是先融合改写两份 Skill。确有硬冲突或执行环境故障会停止，不瞎猜。

本样本是单份整体 Skill，所以没有那个组合检查 turn。技术恢复/索引修复的 Prompt 也不同：仅核对现有文件，不得生图；不能把首次生产 Prompt 冒充所有恢复请求。

公开 SDK item、用量和 thread ID 均由当前任务 Trace 留档；对生图服务真实采用了哪条 image prompt，当前记录是生产者报告与公开事件证据，不宣称能完整审计服务内部提示词。
