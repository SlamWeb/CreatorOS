import { expect, test } from "@playwright/test";

// Controlled read-only transcript: verifies presentation and lazy detail access,
// not model or production correctness. No generation or mutation is requested.
test("batch calls and result links collapse, errors remain visible, details survive reload", async ({ page }, info) => {
  const links = Array.from({ length: 10 }, (_, index) => ({
    url: `/runs/${String(index).padStart(8, "0")}-1977-45c7-a14e-a26dc54af892`,
    label: "查看内容", title: `英语词组 ${index + 1}`, source_tool: "start_content_run", kind: "production",
  }));
  const entries = [
    { id: "u", kind: "user", text: "全都生产", turn_id: "batch" },
    ...links.map((link, index) => ({ id: `tool-${index}`, kind: "tool", name: "start_content_run",
      status: index === 0 ? "failed" : "done", turn_id: "batch", tool_call_id: `call-${index}`,
      model_request_id: "request", links: [link] })),
    { id: "reply", kind: "assistant", text: "已提交，出图后请验收。\n\n" + links.map(link => `[${link.title}](${link.url})`).join(""),
      turn_id: "batch", complete: true, terminal: true, links },
  ];
  const doc = { id: "compact-chat", title: "批量提交", version: 1, status: "idle", error: null,
    scope_kind: "overview", creator_id: null, stream_revision: 1, has_older: false, updated_at: "2026-10-30T08:00:00Z", entries };
  let reads = 0;
  let writes = 0;
  await page.route("**/api/agent/sessions**", async route => {
    if (route.request().method() !== "GET") writes++;
    const path = new URL(route.request().url()).pathname;
    if (path.includes("/requests/")) {
      reads++;
      return route.fulfill({ json: { response: { tool_calls: [{ id: "call-0", name: "start_content_run", arguments: { topic_id: "topic-a" } }] },
        tool_results: [{ tool_call_id: "call-0", name: "start_content_run", content: "所选版本已更新，请重新选择。", is_error: true,
          error_type: "revision_conflict" }], redacted: true } });
    }
    return route.fulfill({ json: path.endsWith("/compact-chat") ? doc : { items: [doc] } });
  });
  await page.goto("/agent?chat=compact-chat");
  const summary = page.getByRole("button", { name: /执行记录.*10 次调用.*1 次需检查/ });
  await expect(summary).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator(".chat-tool-expandable")).toHaveCount(0);
  await expect(page.locator(".chat-answer")).toHaveText("已提交，出图后请验收。");
  await expect(page.getByRole("navigation", { name: "本轮任务入口" })).toHaveCount(0);
  expect(reads).toBe(0);
  await page.getByRole("button", { name: "查看结果（10）" }).click();
  await expect(page.getByRole("navigation", { name: "本轮任务入口" }).getByRole("link")).toHaveCount(10);
  await expect(page.getByRole("link", { name: "英语词组 10", exact: true })).toHaveAttribute("href", links[9].url);
  await summary.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator(".chat-tool-expandable")).toHaveCount(10);
  expect(reads).toBe(0);
  await page.locator('[data-entry-id="tool-0"]').getByRole("button", { name: /提交生产/ }).click();
  await expect(page.getByText("所选版本已更新，请重新选择。", { exact: true })).toBeVisible();
  expect(reads).toBe(1);
  await page.getByRole("button", { name: "收起结果（10）" }).click();
  await summary.click();
  await page.screenshot({ path: info.outputPath("compact-chat-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(summary).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("compact-chat-mobile.png") });
  await page.reload();
  await expect(summary).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByRole("button", { name: "查看结果（10）" })).toHaveAttribute("aria-expanded", "false");
  expect(writes).toBe(0);
});
