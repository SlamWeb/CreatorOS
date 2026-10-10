import { expect, test, type Page } from "@playwright/test";

const sessionId = "research-chat";
const batchId = "batch-one";
const longActivity = `正在核对词义和语境。${"公开工具输出。".repeat(60)}活动末尾`;
const running = () => ({ id: sessionId, title: "英语同义词调研", version: 1, status: "running", error: null,
  scope_kind: "overview", creator_id: null, updated_at: "2026-10-05T12:00:00Z", has_older: false,
  entries: [ { kind: "user", text: "为栏目找 10 组英语同义词。" },
    { kind: "tool", name: "research_series_topics", status: "running", links: [{ url: "/series/series-test?research=batch-one", label: "查看调研", source_tool: "research_series_topics" }], research: {
      id: batchId, status: "researching", note: "正在查找可核验的同义词资料。", url: "/series/series-test?research=batch-one",
      progress: { stage: "searching", last_activity_at: "2026-10-05T12:00:00Z", events: [
        { id: 1, kind: "message", text: "先查看栏目，再核对单词用法。" },
        { id: 2, kind: "tool", text: longActivity },
        { id: 3, kind: "reasoning", text: "内部思考不应展示" },
        { id: 4, kind: "message", text: '<img src="https://example.com/private.png"><script>alert(1)</script>' },
      ] },
    } },
  ],
});
const complete = () => {
  const doc = running();
  doc.version = 3; doc.status = "idle"; doc.updated_at = "2026-10-05T12:00:03Z";
  const tool = doc.entries[1];
  tool.status = "done"; tool.research!.status = "ready"; tool.research!.note = "已找到 10 组候选。";
  tool.research!.progress.stage = "completed";
  doc.entries.push({ kind: "assistant", text: "已完成调研，候选等待你选择，不会自动入队。" });
  return doc;
};

// Deterministic public-event transport injection, never invokes paid Codex research.
async function controlledEvents(page: Page) {
  await page.addInitScript(() => {
    type Source = { url: string; onopen: (() => void) | null; onerror: (() => void) | null;
      listeners: Record<string, ((event: MessageEvent) => void)[]>; closed: boolean };
    const sources: Source[] = [];
    Object.assign(window, { researchTestSources: sources });
    class ControlledSource {
      url: string; onopen: (() => void) | null = null; onerror: (() => void) | null = null;
      listeners: Record<string, ((event: MessageEvent) => void)[]> = {}; closed = false;
      constructor(url: string) { this.url = url; sources.push(this); }
      addEventListener(kind: string, handler: (event: MessageEvent) => void) {
        (this.listeners[kind] ??= []).push(handler);
      }
      close() { this.closed = true; }
    }
    Object.assign(window, { EventSource: ControlledSource });
  });
}
async function emit(page: Page, doc: ReturnType<typeof running>, sourceIndex = -1) {
  await page.evaluate(({ doc, sourceIndex }) => {
    const sources = (window as unknown as { researchTestSources: { listeners: Record<string, ((e: MessageEvent) => void)[]> }[] }).researchTestSources;
    for (const listener of sources.at(sourceIndex)!.listeners.snapshot ?? []) listener(new MessageEvent("snapshot", { data: JSON.stringify(doc) }));
  }, { doc, sourceIndex });
}

test("research remains in chat through progress, disconnect, refresh and ready without resubmission", async ({ page }, info) => {
  await controlledEvents(page);
  await page.clock.install({ time: new Date("2026-10-05T12:00:30Z") });
  let doc = running(); let posts = 0;
  await page.route("**/api/agent/sessions**", async route => {
    if (route.request().method() === "POST") { posts++; return route.fulfill({ status: 500 }); }
    return route.fulfill({ json: new URL(route.request().url()).pathname.endsWith(`/${sessionId}`) ? doc : { items: [doc] } });
  });
  await page.goto(`/agent?chat=${sessionId}`);
  const activity = page.getByRole("region", { name: "选题调研活动" });
  await expect(activity).toContainText("搜索资料");
  await expect(activity).toContainText("先查看栏目，再核对单词用法。");
  await expect(page.getByRole("button", { name: "发送 ↑" })).toBeDisabled();
  await expect(activity).not.toContainText("内部思考不应展示");
  await expect(activity.locator("img, script")).toHaveCount(0);
  await expect(activity.getByText("活动末尾", { exact: false })).not.toBeVisible();
  await activity.getByText("展开活动全文").click();
  await expect(activity.getByText(longActivity, { exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath("research-running-desktop.png") });
  await page.evaluate(() => {
    const source = (window as unknown as { researchTestSources: { onerror: () => void }[] }).researchTestSources.at(-1)!;
    source.onerror();
  });
  await expect(page.getByText("连接中 · 自动刷新", { exact: true })).toBeVisible();
  await page.clock.fastForward(100_000);
  await expect(activity).toContainText("暂无新活动");
  await expect(activity.getByRole("alert")).toHaveCount(0);
  await page.reload();
  await expect(activity).toContainText("搜索资料");
  expect(posts).toBe(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(activity).toContainText("搜索资料");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("research-running-mobile.png") });
  await page.setViewportSize({ width: 1440, height: 900 });
  doc = complete(); await emit(page, doc);
  await expect(activity).toContainText("候选已就绪，尚未入队或生产。");
  await expect(page.getByText("已完成调研，候选等待你选择，不会自动入队。")).toBeVisible();
  await expect(page.getByRole("button", { name: "发送 ↑" })).toBeDisabled(); // Empty composer, not an unfinished request.
  await page.getByRole("textbox", { name: "给 Agent 的消息" }).fill("选第一个候选，先给预览。");
  await expect(page.getByRole("button", { name: "发送 ↑" })).toBeEnabled();
  await expect(activity.getByRole("link", { name: "查看同一调研任务 ↗" })).toHaveAttribute("href", "/series/series-test?research=batch-one");
  await page.reload();
  await expect(activity).toContainText("候选已就绪");
  expect(posts).toBe(0);
  await page.screenshot({ path: info.outputPath("research-ready-desktop.png") });
});

test("failed research explains the concrete error in chat on desktop and mobile", async ({ page }, info) => {
  const doc = running(); doc.status = "idle";
  const tool = doc.entries[1]; tool.status = "failed";
  tool.research!.status = "failed";
  Object.assign(tool.research!, { error: "Codex 可执行文件不可用，请检查本机安装路径。", error_type: "codex_not_found" });
  Object.assign(tool.research!.progress, { stage: "failed", last_activity_at: null, events: [] });
  let posts = 0;
  await page.route("**/api/agent/sessions**", async route => {
    if (route.request().method() === "POST") posts++;
    return route.fulfill({ json: new URL(route.request().url()).pathname.endsWith(`/${sessionId}`) ? doc : { items: [doc] } });
  });
  await page.goto(`/agent?chat=${sessionId}`);
  const activity = page.getByRole("region", { name: "选题调研活动" });
  await expect(activity.getByRole("alert")).toHaveText("Codex 可执行文件不可用，请检查本机安装路径。");
  await expect(activity).not.toContainText("暂无新活动");
  await page.screenshot({ path: info.outputPath("research-failed-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await expect(activity.getByRole("alert")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("research-failed-mobile.png") });
  expect(posts).toBe(0);
});

test("unknown observation keeps the batch link and explains uncertainty without retry", async ({ page }) => {
  const doc = running(); doc.status = "idle";
  const tool = doc.entries[1]; tool.status = "failed"; tool.research!.status = "unknown";
  Object.assign(tool.research!, { error: "状态读取中断，后台结果未知；请查询同一批次，不要重新提交。" });
  let posts = 0;
  await page.route("**/api/agent/sessions**", async route => {
    if (route.request().method() === "POST") posts++;
    return route.fulfill({ json: new URL(route.request().url()).pathname.endsWith(`/${sessionId}`) ? doc : { items: [doc] } });
  });
  await page.goto(`/agent?chat=${sessionId}`);
  const activity = page.getByRole("region", { name: "选题调研活动" });
  await expect(activity).toContainText("调研状态待确认");
  await expect(activity.getByRole("status")).toContainText("后台结果未知");
  await expect(activity.getByRole("link")).toHaveAttribute("href", "/series/series-test?research=batch-one");
  await expect(activity).not.toContainText("候选已就绪");
  await page.reload();
  await expect(activity).toContainText("调研状态待确认");
  expect(posts).toBe(0);
});

test("late read snapshots and closed-session events cannot replace newer research progress", async ({ page }) => {
  await controlledEvents(page);
  let doc = running(); let reads = 0; let deliveredLateRead = false; let release: (() => void) | undefined;
  const lateRead = new Promise<void>(resolve => { release = resolve; });
  const other = { ...complete(), id: "other-chat", title: "另一段对话", entries: [{ kind: "assistant", text: "新会话不会继承旧活动。" }] };
  await page.route("**/api/agent/sessions**", async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith(`/${sessionId}`)) {
      const snapshot = structuredClone(doc); reads++;
      if (reads === 2) await lateRead;
      await route.fulfill({ json: snapshot });
      if (reads === 2) deliveredLateRead = true;
      return;
    }
    return route.fulfill({ json: pathname.endsWith("/other-chat") ? other : { items: [doc, other] } });
  });
  await page.goto(`/agent?chat=${sessionId}`);
  await expect(page.getByRole("region", { name: "选题调研活动" })).toBeVisible();
  await expect.poll(() => reads).toBe(2);
  doc = complete(); await emit(page, doc);
  await expect(page.getByText("已完成调研，候选等待你选择，不会自动入队。")).toBeVisible();
  release!();
  await expect.poll(() => deliveredLateRead).toBe(true);
  await expect(page.getByRole("region", { name: "选题调研活动" })).toContainText("候选已就绪");
  await page.getByRole("button", { name: "另一段对话" }).click();
  await expect(page.getByText("新会话不会继承旧活动。")).toBeVisible();
  await emit(page, running(), 0);
  await expect(page.getByRole("region", { name: "选题调研活动" })).toHaveCount(0);
  await expect(page.getByText("新会话不会继承旧活动。")).toBeVisible();
});
