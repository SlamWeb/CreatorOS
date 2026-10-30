import { expect, test } from "@playwright/test";

// Deterministic UI/fault-injection coverage only. Real DeepSeek/Codex acceptance
// is run separately against an isolated Studio, not claimed by these fixtures.
const runOne = "/runs/6ab3a071-1977-45c7-a14e-a26dc54af892";
const runTwo = "/runs/7ab3a071-1977-45c7-a14e-a26dc54af892";
const links = [
  { url: runOne, label: "查看内容", source_tool: "start_content_run", kind: "production", title: "朱元璋：村口到宫殿", version: 3 },
  { url: runTwo, label: "查看内容", source_tool: "start_content_run", kind: "production", title: "韩信：街市到高坛", version: 2 },
];

test("usage stays out of chat while named links and lazy tool details survive reload", async ({ page }, info) => {
  const doc = { id: "transcript-tools", title: "工具明细验收", version: 1, status: "idle", error: null,
    scope_kind: "overview", creator_id: null, stream_revision: 8, has_older: false, updated_at: "2026-10-22T08:00:00Z",
    entries: [
      { id: "user", kind: "user", text: "看看这两篇。", turn_id: "turn-a" },
      { id: "tool-a", kind: "tool", name: "manage_topics", status: "done", turn_id: "turn-a", tool_call_id: "call-a", model_request_id: "request-a", links: [links[0]] },
      { id: "usage-a", kind: "usage", turn_id: "turn-a", model_request_id: "request-a", input_tokens: 100, output_tokens: 10 },
      { id: "tool-b", kind: "tool", name: "manage_topics", status: "failed", turn_id: "turn-a", tool_call_id: "call-b", model_request_id: "request-a" },
      { id: "usage-b", kind: "usage", turn_id: "turn-a", model_request_id: "request-b", input_tokens: 120, output_tokens: 20 },
      { id: "reply", kind: "assistant", text: "已检查。[查看内容](" + runOne + ")", complete: true, terminal: true, turn_id: "turn-a", model_request_id: "request-b", links: [links[0], links[1], links[0]] },
    ] };
  let reads = 0;
  let writes = 0;
  let failFirst = true;
  await page.route("**/api/agent/sessions**", async route => {
    const url = new URL(route.request().url());
    if (route.request().method() !== "GET") writes++;
    if (url.pathname.includes("/requests/")) {
      reads++;
      if (failFirst) { failFirst = false; return route.fulfill({ status: 503, json: { detail: "受控读取故障" } }); }
      return route.fulfill({ json: {
        response: { tool_calls: [
          { id: "call-a", name: "manage_topics", arguments: { action: "get", topic_id: "原选题甲" } },
          { id: "call-b", name: "manage_topics", arguments: { action: "update", topic_id: "原选题乙" } },
        ] },
        tool_results: [
          { tool_call_id: "call-a", name: "manage_topics", content: "甲的独立结果", is_error: false },
          { tool_call_id: "call-b", name: "manage_topics", content: "乙的冲突错误：" + "长文".repeat(900),
            raw_content: "脱敏原始返回 [REDACTED]", is_error: true, error_type: "revision_conflict" },
        ], redacted: true,
      } });
    }
    if (url.pathname.endsWith("/transcript-tools")) return route.fulfill({ json: doc });
    return route.fulfill({ json: { items: [doc] } });
  });
  await page.goto("/agent?chat=transcript-tools");
  await expect(page.getByRole("button", { name: /本条请求用量/ })).toHaveCount(0);
  await expect(page.locator(".chat-turn-usage")).toHaveCount(0);
  const navigation = page.getByRole("navigation", { name: "本轮任务入口" });
  await expect(navigation.getByRole("link")).toHaveCount(2);
  await expect(page.getByRole("link", { name: "朱元璋：村口到宫殿 · 第 3 版", exact: true })).toHaveCount(1);
  await expect(page.getByRole("link", { name: "韩信：街市到高坛 · 第 2 版", exact: true })).toHaveAttribute("href", runTwo);
  expect(reads).toBe(0);
  await page.getByRole("button", { name: /执行记录.*2 次调用.*1 次需检查/ }).click();
  const firstTool = page.locator('[data-entry-id="tool-a"]');
  const firstToggle = firstTool.getByRole("button", { name: /管理选题/ });
  await firstToggle.focus();
  await page.keyboard.press("Enter");
  await expect(firstTool.getByRole("alert")).toContainText("受控读取故障");
  await firstTool.getByRole("button", { name: "重新读取" }).click();
  await expect(firstTool.getByText("甲的独立结果", { exact: true })).toBeVisible();
  await expect(firstTool.getByText("原选题乙", { exact: false })).toHaveCount(0);
  await firstToggle.click();
  const secondTool = page.locator('[data-entry-id="tool-b"]');
  await secondTool.getByRole("button", { name: /管理选题/ }).click();
  await expect(secondTool.getByText("错误类型：revision_conflict")).toBeVisible();
  await expect(secondTool.locator("code").first()).toContainText("原选题乙");
  await expect(secondTool.locator("code").nth(1)).not.toHaveText("乙的冲突错误：" + "长文".repeat(900));
  await secondTool.getByRole("button", { name: /展开全文/ }).click();
  await expect(secondTool.locator("code").nth(1)).toHaveText("乙的冲突错误：" + "长文".repeat(900));
  await expect(secondTool.getByText("脱敏原始返回 [REDACTED]", { exact: true })).toHaveCount(0);
  await secondTool.getByRole("button", { name: "查看原始返回" }).click();
  await expect(secondTool.getByText("脱敏原始返回 [REDACTED]", { exact: true })).toBeVisible();
  await secondTool.getByRole("button", { name: /管理选题/ }).click();
  await firstToggle.click();
  await page.locator(".agent-transcript").evaluate(element => { element.scrollTop = 0; });
  await page.screenshot({ path: info.outputPath("chat-tools-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(secondTool.getByRole("button", { name: /管理选题/ })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("chat-tools-mobile.png") });
  await page.reload();
  await expect(page.getByRole("button", { name: /本条请求用量/ })).toHaveCount(0);
  await expect(page.locator(".chat-tool-details")).toHaveCount(0);
  await expect(navigation.getByRole("link")).toHaveCount(2);
  expect(writes).toBe(0);
});

test("stream patches preserve expanded tools, ignore replay, and recover gaps without writes", async ({ page }) => {
  await page.clock.install();
  await page.addInitScript(() => {
    type TestSource = EventTarget & { closed: boolean; onopen: ((event: Event) => void) | null;
      onerror: ((event: Event) => void) | null; close: () => void };
    const sources: TestSource[] = [];
    class ControlledSource extends EventTarget {
      closed = false;
      onopen: ((event: Event) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;
      constructor() { super(); sources.push(this); queueMicrotask(() => this.onopen?.(new Event("open"))); }
      close() { this.closed = true; }
    }
    Object.defineProperty(window, "EventSource", { value: ControlledSource });
    Object.assign(window, { __streamEmit: (kind: string, value: unknown) => {
      for (const source of sources.filter(item => !item.closed)) source.dispatchEvent(new MessageEvent(kind, { data: JSON.stringify(value) }));
    } });
  });
  const initial = { id: "patch-chat", title: "增量验收", version: 1, status: "running", error: null,
    scope_kind: "overview", creator_id: null, stream_revision: 1, has_older: false, updated_at: "2026-10-22T08:00:00Z",
    entries: [{ id: "old-user", kind: "user", text: "历史讨论\n".repeat(65), turn_id: "older-turn" },
      { id: "user", kind: "user", text: "检查一下", turn_id: "turn" },
      { id: "tool", kind: "tool", name: "manage_topics", status: "running", turn_id: "turn", tool_call_id: "call", model_request_id: "request" },
      { id: "tool-two", kind: "tool", name: "manage_topics", status: "done", turn_id: "turn", tool_call_id: "call-two", model_request_id: "request" },
      { id: "reply", kind: "assistant", text: "正在", turn_id: "turn", complete: false, terminal: false }] };
  let latest = initial;
  let reads = 0;
  let writes = 0;
  await page.route("**/api/agent/sessions**", route => {
    const url = new URL(route.request().url());
    if (route.request().method() !== "GET") writes++;
    if (url.pathname.includes("/requests/")) return route.fulfill({ json: {
      response: { tool_calls: [{ id: "call", name: "manage_topics", arguments: { action: "get" } }] },
      tool_results: latest.stream_revision >= 2 ? [{ tool_call_id: "call", name: "manage_topics", content: "已核对原选题", is_error: false }] : [],
    } });
    if (url.pathname.endsWith("/patch-chat")) { reads++; return route.fulfill({ json: latest }); }
    return route.fulfill({ json: { items: [latest] } });
  });
  const emit = async (kind: string, value: unknown) => page.evaluate(({ kind, value }) => {
    (window as unknown as { __streamEmit: (kind: string, value: unknown) => void }).__streamEmit(kind, value);
  }, { kind, value });
  await page.goto("/agent?chat=patch-chat");
  await expect(page.getByText("正在", { exact: true })).toBeVisible();
  await emit("snapshot", initial);
  const groupToggle = page.getByRole("button", { name: /执行记录.*2 次调用/ });
  await groupToggle.click();
  const toggle = page.locator('[data-entry-id="tool"]').getByRole("button", { name: /管理选题/ });
  await toggle.click();
  await expect(page.getByText("尚未返回结果。", { exact: true })).toBeVisible();
  latest = { ...initial, stream_revision: 2, entries: initial.entries.map(entry => entry.id === "reply" ? { ...entry, text: "正在核对" }
    : entry.id === "tool" ? { ...entry, status: "done" } : entry) };
  const patch = { id: "patch-chat", base_revision: 1, stream_revision: 2,
    entries_upsert: [{ id: "tool", status: "done" }], entries_text_append: [{ id: "reply", text: "核对" }] };
  await emit("patch", patch);
  await expect(page.getByText("正在核对", { exact: true })).toBeVisible();
  await expect(groupToggle).toHaveAttribute("aria-expanded", "true");
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByText("已核对原选题", { exact: true })).toBeVisible();
  await emit("patch", patch);
  await expect(page.locator(".chat-answer")).toHaveText("正在核对");
  const beforeClock = reads;
  await page.clock.fastForward(2600);
  expect(reads).toBe(beforeClock);
  const transcript = page.locator(".agent-transcript");
  await transcript.evaluate(element => { element.scrollTop = 0; element.dispatchEvent(new Event("scroll", { bubbles: true })); });
  expect(await transcript.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true);
  latest = { ...latest, stream_revision: 3, entries: latest.entries.map(entry => entry.id === "reply" ? { ...entry, text: "正在核对，不打断阅读" } : entry) };
  await emit("patch", { id: "patch-chat", base_revision: 2, stream_revision: 3, entries_upsert: [], entries_text_append: [{ id: "reply", text: "，不打断阅读" }] });
  await expect(page.locator(".chat-answer")).toHaveText("正在核对，不打断阅读");
  expect(await transcript.evaluate(element => element.scrollTop)).toBe(0);
  latest = { ...latest, stream_revision: 5, entries: latest.entries.map(entry => entry.id === "reply" ? { ...entry, text: "正在核对，完整恢复" } : entry) };
  await emit("patch", { ...patch, base_revision: 4, stream_revision: 5 });
  await expect(page.locator(".chat-answer")).toHaveText("正在核对，完整恢复");
  await expect(groupToggle).toHaveAttribute("aria-expanded", "true");
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  expect(reads).toBe(beforeClock + 1);
  expect(writes).toBe(0);
});

test("legacy usage stays hidden and unlinked tool details do not trigger trace lookup", async ({ page }) => {
  let detailsRead = 0;
  const doc = { id: "legacy-chat", title: "历史记录", version: 1, status: "idle", error: null,
    scope_kind: "overview", creator_id: null, has_older: false, updated_at: "2026-10-22T08:00:00Z",
    entries: [{ kind: "usage", input_tokens: 50 }, { kind: "tool", name: "manage_topics", status: "done" },
      { kind: "assistant", text: "旧回复。", complete: true }] };
  await page.route("**/api/agent/sessions**", route => {
    const url = new URL(route.request().url());
    if (url.pathname.includes("/turn-trace/")) detailsRead++;
    return route.fulfill({ json: url.pathname.endsWith("/legacy-chat") ? doc : { items: [doc] } });
  });
  await page.goto("/agent?chat=legacy-chat");
  await expect(page.getByRole("button", { name: "历史调用用量（未关联轮次）" })).toHaveCount(0);
  await expect(page.locator(".chat-turn-usage")).toHaveCount(0);
  await page.getByRole("button", { name: /管理选题/ }).click();
  await expect(page.getByText("这条旧记录没有关联工具详情；完整历史可在回复 Trace 中查看。")).toBeVisible();
  expect(detailsRead).toBe(0);
});
