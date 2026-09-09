import { expect, test } from "@playwright/test";

test("candidate browsing keeps edits separate from selection", async ({ page }, testInfo) => {
  const creator = await page.request.post("/api/creators", {
    data: { display_name: "Research E2E", account_handle: "research_e2e", daily_content_limit: 1 },
  }).then((response) => response.json());
  const series = await page.request.post(`/api/creators/${creator.id}/series`, {
    data: { name: "候选实验室", description: "验证候选浏览和编辑。", audience: "测试用户" },
  }).then((response) => response.json());
  const batch = {
    id: "batch-ui-e2e",
    series_id: series.id,
    status: "ready",
    stale: false,
    note: "",
    created_at: "2026-09-09T09:00:00+08:00",
    attempt: 1,
    candidates: [
      { id: "c1", title: "原始候选一", angle: "先讲清问题", rationale: "来源一", sources: [{ title: "来源一", url: "https://example.com/1" }], queued: false },
      { id: "c2", title: "原始候选二", angle: "再给出方法", rationale: "来源二", sources: [{ title: "来源二", url: "https://example.com/2" }], queued: false },
    ],
  };
  await page.route("**/api/series/*/topic-research", async (route) => {
    if (route.request().method() === "GET") return route.fulfill({ json: { items: [batch] } });
    return route.fallback();
  });
  await page.route("**/api/topic-research/batch-ui-e2e", async (route) => route.fulfill({ json: batch }));

  await page.goto(`/series/${series.id}`);
  await expect(page.getByRole("heading", { name: "选题候选" })).toBeVisible();
  const first = page.getByTestId("candidate-c1");
  await expect(first).toBeVisible();

  await first.getByRole("checkbox").check();
  await expect(first.locator(".candidate-editor")).toBeHidden();
  await first.getByRole("button", { name: "编辑候选 c1" }).click();
  await expect(first.locator(".candidate-editor")).toBeVisible();
  await first.locator(".candidate-editor input").fill("改过的候选一");
  await first.getByRole("button", { name: "保存修改" }).click();
  await expect(first.getByRole("heading", { name: "改过的候选一" })).toBeVisible();

  await first.getByRole("checkbox").uncheck();
  await first.getByRole("checkbox").check();
  await expect(first.getByRole("heading", { name: "改过的候选一" })).toBeVisible();

  await page.getByTestId("candidate-c2").getByRole("checkbox").check();
  await page.getByText(/调整入队顺序/).click();
  await page.getByRole("button", { name: "c2 上移" }).click();
  const order = await page.locator(".research-order > div span").allTextContents();
  expect(order[0]).toContain("原始候选二");
  expect(order[1]).toContain("改过的候选一");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: testInfo.outputPath("candidate-mobile.png"), fullPage: true });
  const dimensions = await page.evaluate(() => ({ body: document.body.scrollWidth, viewport: window.innerWidth }));
  expect(dimensions.body).toBeLessThanOrEqual(dimensions.viewport);
});
