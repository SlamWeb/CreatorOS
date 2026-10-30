# 产物提交工具：前后对比图

使用内置 image_gen 生成；参考用户提供的白底、手绘线条、浅红/绿/蓝/紫分区风格，不复制原图内容或署名。

最终图：`submit-artifacts-comparison.png`。

## 图意

左侧：生成文件 → 接收保留 → 最终清单并结束回合 → 宿主核验 → 清单错误需要额外恢复。
右侧：生成文件 → submit_artifacts → 宿主核验 → 拒绝则返回具体错误并在本回合修正 → 成功回执 → 最终回复。

两边均需要程序验收。工具化改变反馈时机，不代表旧协议仅凭模型声称完成，也不代表文件校验能评判内容质量。图片仍复用 SDK 事件收图；视频在提交时验证并接收文件，图中左侧的接收方框概括文件保留，不表示两种媒体都有同类 SDK 事件。

## 生成提示词摘要

Chinese engineering infographic, wide landscape, white paper, hand-drawn charcoal arrows, pastel salmon/orange left panel and blue/green right panel. Title: 图片已经生成，为什么还会交付失败？ Subtitle: 把交付校验放进 Agent 的执行循环. Left: 没有提交工具：结束后才核验. Right: 有提交工具：执行中当场核验. Rejection arrow loops from CreatorOS校验 through 返回具体错误→修正清单 to submit_artifacts before final reply. Bottom: 收到文件≠交付完成；程序核对文件，模型决定顺序与内容。提交工具不等于内容质量评审。仍需：重复提交去重·保存回执·限制重试次数. No invented metrics or watermark.

定向修订：底部完成依据对比改为“结束后核验 → 结束前拿到回执”，避免暗示旧协议没有程序校验。
