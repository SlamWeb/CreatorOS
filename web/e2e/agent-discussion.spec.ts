import { expect, test } from "@playwright/test";

const sessionId = "discussion-chat";
const runId = "6ab3a071-1977-45c7-a14e-a26dc54af892";
const snapshot = {
  id: sessionId, title: "核对已验收作品", version: 2, status: "idle", error: null,
  scope_kind: "overview", creator_id: null, updated_at: "2026-10-08T12:00:00Z", has_older: false,
  entries: [
    { kind: "user", text: "讨论这个作品的事实准确性。" },
    { kind: "tool", name: "discuss_content_run", status: "done", discussion: {
      id: "discussion-one", run_id: runId, revision_id: "revision-one", status: "completed",
      reply: "核对结果：\n\n- 关键术语定义清晰。\n- 第二页例子建议再核实来源。\n\n![外部图片](https://example.invalid/tracker.png)",
      error: null, updated_at: "2026-10-08T12:00:00Z",
      events: [
        { id: 1, kind: "status", text: "讨论已开始。" },
        { id: 2, kind: "message", text: "<script>这段文本不应执行</script>" },
      ],
    } },
  ],
};

test("account Agent shows read-only discussion result and its version-scoped link", async ({ page }, info) => {
  await page.route("**/api/agent/sessions**", route => route.fulfill({ json: route.request().url().endsWith(`/${sessionId}`) ? snapshot : { items: [snapshot] } }));
  await page.goto(`/agent?chat=${sessionId}`);
  const activity = page.getByRole("region", { name: "作品讨论进度" });
  await expect(activity).toContainText("讨论已完成");
  await expect(activity).toContainText("第二页例子建议再核实来源。");
  await expect(activity).toContainText("这段文本不应执行");
  await expect(activity.locator("script")).toHaveCount(0);
  await expect(activity.locator("img")).toHaveCount(0);
  await expect(activity.getByRole("link", { name: "查看同一讨论 ↗" })).toHaveAttribute("href", `/runs/${runId}?discussion=discussion-one&revision=revision-one`);
  await page.screenshot({ path: info.outputPath("agent-discussion-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("agent-discussion-mobile.png"), fullPage: true });
});
