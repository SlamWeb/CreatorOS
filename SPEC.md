# CreatorOS Runtime SPEC

Progressive SPEC, not a form.

## 当前理解

- CreatorOS 是面向创作者 / MCN 场景的长期项目；当前阶段只从零学习并手写极简 Python Agent Runtime。
- 用户具备 Python 基础和简单 Agent Loop 经验，希望逐层理解 Agent Harness / Runtime，并能在求职和面试中讲清工程取舍。
- Pi Agent 只作为架构参考；实现应从 Python 的最小可运行版本逐步演进。

## 本轮目标

本轮只迈一个 `small step`：

- 建立根级 `AGENTS.md` 和 `SPEC.md`，固化学习方式、Pi 参考资料、范围边界、验证方式和 Git 提交流程。
- 不创建 Runtime 代码，不提前搭建完整目录结构。

## 当前假设

- 第一段 Runtime 代码将只验证一次 OpenAI-compatible LLM 调用，不包含循环、工具或抽象层。
- 初始实现可能使用 DeepSeek API；在用户真正输入代码前，这仍是可调整假设。
- 项目早期所有模块共享根级 `SPEC.md`；出现稳定模块边界后，再在最近模块建立独立 `SPEC.md`。

## 对外影响

- 本轮只新增仓库内协作文档，不产生运行时行为、API、数据或配置变更。
- GitHub 仓库将获得第一组可追踪文档历史。

暂未确认：

- 首个模型调用最终使用的模型、依赖版本和 Python 版本。
- Runtime 包名、目录结构和公共接口；这些都不在本轮决定。

## 验收与验证草案

- `AGENTS.md` 明确“一次一个概念、用户亲手编码、先解释后实现、验证后再推进”。
- `AGENTS.md` 保留 Pi 源码与文档链接，并记录不 fork、不照抄 TypeScript 的学习方式。
- `SPEC.md` 清楚区分当前目标、假设、影响和暂不实现的范围。
- 仓库中没有因本轮而新增 Runtime 代码或秘密信息。

优先运行：

```powershell
git diff --check
git status --short
```

## 最近验证

- 日期：2026-08-21
- 命令：`git diff --cached --check`、`git diff --cached --stat`、PowerShell 文件清单与关键规则检查
- 结果：通过；暂存区只有 `AGENTS.md` 和 `SPEC.md`，共新增 100 行，未发现空白错误，Pi 两个链接和 Git 闭环均存在
- 问题：仓库尚无首个 commit，远端没有可用于创建 PR 的基线分支；本轮先推送独立文档分支
