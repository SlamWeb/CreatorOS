---
name: CreatorOS
description: 白底、石墨文字与蓝色 C 标组成的中文创作入口和渐进迁移中的工作台。
colors:
  canvas: "#ffffff"
  surface: "#f5f5f7"
  ink: "#1d1d1f"
  supporting-text: "#62626b"
  line: "#d2d2d7"
  accent: "#0071e3"
  accent-strong: "#0060c9"
  secondary-line: "#cbdff7"
  action-hover: "#f4f8ff"
  brand-blue: "#2277f5"
  brand-light: "#74b7ff"
typography:
  display:
    fontFamily: '"Inter Variable", "Inter", -apple-system, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", system-ui, sans-serif'
    fontSize: "clamp(48px, 6.8vw, 96px)"
    fontWeight: 700
    lineHeight: 1.18
    letterSpacing: "-0.035em"
  headline:
    fontSize: "30px"
    fontWeight: 600
    lineHeight: 1.35
    letterSpacing: "-0.025em"
  body:
    fontFamily: '"Inter Variable", "Inter", -apple-system, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", system-ui, sans-serif'
    fontSize: "clamp(16px, 1.5vw, 20px)"
    fontWeight: 400
    lineHeight: 1.8
  label:
    fontSize: "13px"
    fontWeight: 500
rounded:
  sm: "10px"
  md: "14px"
  lg: "20px"
spacing:
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "32px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.canvas}"
    typography: "{typography.label}"
    height: "36px"
    padding: "8px 18px"
  button-primary-hover:
    backgroundColor: "{colors.accent-strong}"
  button-secondary:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.accent}"
    typography: "{typography.label}"
    height: "36px"
    padding: "8px 18px"
  button-secondary-hover:
    backgroundColor: "{colors.action-hover}"
    textColor: "{colors.accent-strong}"
  button-link:
    backgroundColor: "transparent"
    textColor: "{colors.accent}"
    height: "36px"
    padding: "8px 18px"
  input:
    backgroundColor: "{colors.canvas}"
    rounded: "{rounded.sm}"
    height: "44px"
    padding: "8px 12px"
---

# Design System: CreatorOS

## Overview

**Creative North Star: "留白中的创作入口"**

白底、石墨文字与既有蓝色 C 标构成当前视觉身份。中文入口主页采用用户确认的大留白和大标题，让主动作清楚、账号入口可读；此构图属于首页，不规定所有业务页都使用大标题。

通用次级动作采用白底蓝边。工作台继续使用现有浅灰容器和旧 CSS，按页面迁移到本地 UI 组件；本文记录已实现的入口和公共控件增量，不代表全站完成重设计。

**Key Characteristics:**
- 白色画布、石墨正文和少量蓝色动作。
- 既有 C 标保留自己的双蓝色，不替换为另一套品牌图形。
- 首页疏朗，业务工作台按真实内容保持所需密度。
- 次级按钮白底蓝边，按钮和首页链接悬浮无下划线。

## Colors

单一动作蓝配合中性画布；C 标的两种蓝仅属于现有标识。Sidecar 的八级色带是供面板预览的 OKLCH 推导色带，不是应用新增的规范色阶。

### Primary
- **动作蓝**：工作台主按钮、文字动作和焦点；首页仅 Logo 保留品牌双蓝。
- **深动作蓝**：主按钮及文字动作的悬浮反馈。
- **品牌双蓝**：沿用 `BrandMark` 的两个 SVG 填色，不重新绘制。

### Neutral
- **白色画布**：页面与次级按钮底色。
- **石墨正文**：标题与主要内容；**辅助文字**用于首页说明和账号副标题。
- **浅灰表面**：既有工作台容器仍可使用。
- **分隔线**：首页账号列表与页脚；**次级描边**用于次级按钮和账号头像框。
- **浅蓝悬浮底**：次级按钮与首页账号行。

**The Action Blue Rule.** 工作台主次动作保留蓝色可辨识性，不恢复灰底通用次级按钮；首页单独采用用户确认的中性配色。

## Typography

**Display Font / Body Font:** Inter Variable / Inter，后接系统及中文字体 fallback；实际中文字形取决于本机可用字体。

前述 display、headline 和 body 是首页角色，不覆盖工作台已有标题字号。首页大标题为两行，正文保持较松行距；公共按钮和输入框使用紧凑文字。技术信息继续使用现有等宽字体，未在本次提取为新层级。

## Layout

首页采用居中流式容器：导航最大宽度（1440px），hero（1160px），账号区（1120px）。导航桌面左右内边距（56px）；账号区左右内边距（32px），双列列表列间距（48px）。首屏保留大面积留白；账号区是后续入口，不把工作台账号树搬到首页。首页没有页脚小字、URL 箭头或重复 CTA。

首页在宽度不超过（700px）时，保留紧凑的文字导航与单列账号区。移动大标题使用（clamp(39px, 8.5vw, 58px)），正文（15px），区标题（26px）。hero 唯一主动作桌面高度（52px），手机（48px），覆盖公共按钮默认高度。

## Elevation & Depth

首页依靠留白、细线和文字层级组织内容，没有卡片投影。主题仍保留既有小、中、大三级阴影供其他工作台界面使用；不据首页推导全站禁用阴影，也不新增装饰性光晕。焦点环是交互反馈，不是常驻装饰。

## Shapes

公共按钮默认胶囊形；首页唯一大 CTA 同样采用胶囊形。输入框使用主题小号圆角。账号头像框为轻圆角（12px），账号入口本身是有分隔线的行，不是填满浅灰底的卡片。

## Components

### Buttons

主按钮蓝底白字，悬浮使用深动作蓝。次级按钮白底蓝字及细描边（1px），悬浮改为浅蓝底、动作蓝描边和深蓝文字。公共 link / quiet 变体通过颜色或底色反馈，悬浮不出现下划线；两代控件仍并存，旧 quiet 与新 link 的浅色背景值并非完全相同。

首页是用户确认的中性色例外：仅 Logo 蓝色；标题近黑，说明深灰，唯一 CTA 黑底白字，hover 为 #363638；重试/空态采用白底中性描边。工作台保留以上蓝色动作。新 Button 的工作台焦点环仍为（3px，动作蓝 30%）；首页使用石墨外轮廓（2px，偏移 5px），主 CTA 不叠加蓝色焦点环。原生 disabled 与导航语义保留。

### Inputs / Fields

本地 Input 为白底、细线、主题小号圆角，高度见 token；键盘焦点改蓝色边框和焦点环（3px，动作蓝 50%）。Textarea 沿用同一材质、最小高度（64px），焦点环透明度（30%）。错误/禁用来自现有原生或 ARIA 状态，不新增一套表单行为。

### Navigation

首页文字导航桌面（14px / 500），手机（13px），悬浮改为深灰，不加箭头或下划线；小屏完整保留导航。跳至正文链接仅聚焦时进入视野。工作台原侧栏继续保留，不套用首页导航布局。

### Account Rows / Containers

首页行展示真实账号名和栏目摘要，长内容截断；无账号、读取中和失败状态使用文字及可用入口，不填假内容。工作台既有浅灰容器未在本次删除或重新定为首页组件。

### Homepage Motion

标题首次挂载时以 650ms 柔焦显现，两行相隔 90ms，默认内容可见。标题内用本地透明底卡通作品卡片与拼接模块，暖黄色、淡紫色和圆润轮廓表达创作资产；生成提示词随素材保存，无外框，不伪装成按钮，保留既有悬浮轻动。此前 Fluent 和写实图标保留以便回退。唯一黑色入口磁吸最多水平 5px、垂直 3px，移出、键盘聚焦或页面转后台归位。只用 CSS 和局部指针事件，不新增动效依赖；触屏保持静态，减少动态效果模式取消位移动画。工作台不套用这套动效。

### Evidence Boundary

记录依据：`theme.css`、`ui.css`、`styles.css` 按钮区、本地 Button/Input/Textarea、`BrandMark`、`HomePage.tsx` 和 `home.css`，产品确认见 `PRODUCT.md`。入口隔离浏览器回归两项通过；已有产物链路另有三项通过，均不证明模型产出质量。

首页截图覆盖桌面（1440×900）、手机（390×844）及宽屏（2550×1292）。浏览器断言覆盖主/次按钮默认颜色、次按钮 hover 无下划线，以及主动作聚焦和 Enter 导航；外轮廓另有聚焦截图证据。hover 的全部色值、所有按钮变体、disabled 和 reduced-motion 未逐项实测：这些仅有源码依据。首页 reduced-motion 会关闭其后代 transition，不能据此宣称全站动效均已验收。截图和操作证据位于 `tmp/entry-home-20261101/final-results/`，测试入口为 `web/e2e/home-page.spec.ts`。

后续动效接入专项 3/3（31.2s）：新增实际悬浮图标/按钮、位移归位、键盘导航、减少动态效果和 320px 触屏点按断言，桌面/手机/宽屏截图已查看；上述 reduced-motion 未测描述仅适用于上一轮。证据独立存于 `tmp/home-motion-20261101/final-results/`，没有覆盖工作台或真实模型生产。

## Do's and Don'ts

### Do:
- **Do** 复用主题变量和既有 C 标；公共控件沿用本地 UI 组件。
- **Do** 用白底蓝边表达通用次级动作，并保留可见键盘焦点。
- **Do** 按页面迁移旧 CSS，保持现有工作台容器与真实状态可读。

### Don't:
- **Don't** 为按钮或首页导航添加悬浮下划线，或恢复通用灰底次级按钮。
- **Don't** 把首页的大留白构图推广为业务页必须遵守的密度规则。
- **Don't** 将既有灰色卡片全部视为缺陷，或把局部验收写成全站验证。
