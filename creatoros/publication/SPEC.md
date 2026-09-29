# 人工发布与反馈 SPEC

## 用户目标

沿现有 `ContentRun` 完成一次真实的“产物验收 → 人工发布 → 效果回填”。发布由用户在自己的小红书账号完成；CreatorOS 只记录发布凭证与后续手填指标，不调用平台发布/分析接口。

## 最小契约

- 仅 `approved` Run 可登记一条小红书发布记录，绑定已批准的 Revision 与产物 digest；要求笔记链接，重复提交相同记录幂等，冲突记录拒绝。
- 发布记录后把 Topic 标记 `published`，不把 `ContentRun.approved` 误写成平台发布状态。
- 一条发布记录可追加多次指标快照，记录采集时间和阅读、点赞、收藏、评论、分享；未提供的指标保持未知，不当作 0。重复请求以 request_id 去重。
- Run Inspector 展示登记入口、真实链接和指标历史；不自动抓取、自动发布或根据指标自动改选题。
- 数据库是发布与反馈真相，`outputs/` 仍只存图片/文案/生产证据。

## 验收

隔离 SQLite 从 Alembic 升级；非 approved/错版本/错 digest/坏链接拒绝且零写入；重复发布不产生第二条；指标负数拒绝、相同 request_id 不重复；重启后通过 HTTP 读取；浏览器完成批准后的登记、刷新及回填。正式库只在启动迁移，不写入虚构发布或指标。

## 实施与验证（2026-09-30）

- Alembic `20260930_0007` 新增一条 Run 对一条人工发布凭证、可多次追加的指标快照；发布凭证绑定已批准的 Revision/digest，Topic 在同事务标记 published，Run 状态仍 approved。此模块不调用任何平台 API。
- Run Inspector 在批准后提供受摘要校验的图片/文案 ZIP、手填小红书笔记链接和分时指标；已登记时显示“已发布”，未登记时明确“尚未发布”。指标未填保持 NULL。链接仅校验格式与允许的域名，不验证平台笔记真实存在。
- `python -m tests.smoke_manual_publication`：隔离迁移与 ORM 零 drift、未批准/错摘要/坏链接拒绝、ZIP 5 图、重复登记及指标 request_id 幂等、刷新可读取，均通过。现有 `smoke_studio_artifacts` 通过。
- Playwright `studio-workflow.spec.ts` 隔离全流程 1 passed：批准→登记模拟链接→回填→刷新；桌面/390px 截图已检查且手机无横向溢出。隔离链接不是真实小红书发布，也没有真实数据。
- 正式数据库在启动前复制备份至忽略的 `tmp/creatoros-before-manual-publication-20260930.db`；随后服务启动迁移到 0007。正式消息队列 Run 仍 `awaiting_approval`、6 张图、无 publication，不自动批准、发布或回填。

## 后续边界

- 下一步由用户逐张验收真实产物并手动发布，回到同一 Run 填真实笔记链接与 T+1/T+7 数据；届时才可称第一条真实闭环完成。
- 本轮不做链接真实性抓取、自动发布、平台授权、自动指标抓取或反馈驱动选题；Run 事件时间线尚未把人工发布/指标表并入统一 Trace，当前在面板单独呈现。
