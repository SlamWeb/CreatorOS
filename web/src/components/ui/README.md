# CreatorOS UI

本地维护的 shadcn/ui 组件源码，使用 React 19、Tailwind v4 与 `@radix-ui/react-slot`。
通过官方 CLI `shadcn add button input textarea` 引入，再适配 CreatorOS 主题。不是黑盒组件包，不要求复制官方默认外观。

- `Button`：primary/secondary/ghost/link/outline/destructive；危险动作使用 destructive。
- `Input`、`Textarea`：保留原生 ref、受控值、验证、输入法和表单事件。
- 导航用 `<Button asChild><Link ... /></Button>`；下载用原生链接或原有下载处理器。
- 按钮明确写 `type="button"` 或 `type="submit"`，组件不替业务决定何时提交。
- `cn` 位于 `@/lib/utils`，合并条件类和 Tailwind 冲突类。

颜色从 `theme.css` 映射到 `ui.css`，不要重新定义一套蓝色或 Logo。
`ui.css` 不启用 Preflight；在迁移期间使用不分层的 utilities，与旧的不分层 CSS 共存。
旧页面先保留原样式，每迁一个组件再删除对应旧规则；不要一次把原生 select、checkbox、图片查看器全部换掉。
新组件避免继续依赖 `.button-primary` 等旧变体类；仍影响新旧组件的布局规则使用 data-slot 选择器并行过渡。

当前已迁移：RunInspector 常用动作、返工/人工发布表单、RunControls、ArtifactDownload、公共 ErrorState 重试按钮。
保留：图片浏览器、版本原生选择、标签页、现有弹窗，以及其余页面的旧控件；后续按实际页面逐批迁移。

来源：[shadcn/ui](https://ui.shadcn.com/docs/installation/vite)、[Tailwind](https://tailwindcss.com/docs/preflight)。
依赖安装：`npm ci`；验证：`npm run typecheck`、`npm run build`。
浏览器专项：隔离 dist 下执行 `playwright test e2e/ui-foundation.spec.ts`，详见 web/SPEC.md。
