import { expect, test } from "@playwright/test";

test("candidate browsing keeps edits separate from selection", async ({ page }, testInfo) => {
  const creator = await page.request.post("/api/creators", {
    data: { display_name: "Research E2E", account_handle: "research_e2e", daily_content_limit: 1 },
  }).then((response) => response.json());
  const series = await page.request.post(`/api/creators/${creator.id}/series`, {
    data: { name: "候选实验室", description: "验证候选浏览和编辑。", audience: "测试用户" },
  }).then((response) => response.json());
  const seeded = await page.request.post(`/test/series/${series.id}/research`, { data: {} });
  expect(seeded.ok(), await seeded.text()).toBeTruthy();
  const batch = await seeded.json();
  expect(batch.candidates).toHaveLength(2);
  await page.goto(`/series/${series.id}?research=${batch.id}`);
  await expect(page.getByRole("region", { name: "选题候选" })).toBeVisible();
  await expect(page.getByLabel("调研记录")).toHaveValue(batch.id);
  await expect(page.getByTestId("candidate-c1")).toBeVisible();
  await page.getByRole("button", { name: "← 返回选题库" }).click();
  expect(new URL(page.url()).searchParams.has("research")).toBe(false);
  await page.goto(`/series/${series.id}`);
  await expect(page.getByRole("heading", { name: "内容" })).toBeVisible();
  await page.getByRole("button", { name: "已入队", exact: true }).click();
  await expect(page.getByText("还没有已入队选题。")).toBeVisible();
  await page.getByRole("button", { name: "待选", exact: true }).click();
  await page.reload();
  await expect(page.getByRole("button", { name: "待选", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: "开始生产" })).toHaveCount(0);
  await expect(page.locator(".content-card")).toHaveCount(2);
  await expect(page.locator(".library-pending")).toHaveText(["待选", "待选"]);
  await expect(page.getByText("已过期", { exact: false })).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("library-desktop.png"), fullPage: true });
  const firstPending = page.getByTestId(`library-research-${batch.id}-c1`);
  await firstPending.getByRole("button", { name: /查看选题/ }).click();
  const detail = page.getByRole("dialog", { name: "选题详情" });
  await expect(detail.getByRole("heading", { name: batch.candidates.find((candidate: { id: string }) => candidate.id === "c1")!.title })).toBeVisible();
  await detail.getByRole("button", { name: "挑选本批次" }).click();
  await expect(page.getByRole("heading", { name: "选择本批次选题" })).toBeVisible();
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
  expect(order[0]).toContain("上下文");
  expect(order[1]).toContain("改过的候选一");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: testInfo.outputPath("candidate-mobile.png"), fullPage: true });
  const dimensions = await page.evaluate(() => ({ body: document.body.scrollWidth, viewport: window.innerWidth }));
  expect(dimensions.body).toBeLessThanOrEqual(dimensions.viewport);
  await page.getByRole("button", { name: "预览", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "运营指令" });
  await expect(dialog.getByText("等待确认")).toBeVisible();
  expect((await page.request.get(`/api/series/${series.id}/topics`).then(r => r.json())).page.total).toBe(0);
  await page.keyboard.press("Escape");
  // A 策略：显性勾选后确认入队直接写入，不再强制人工确认抽屉。
  await page.getByRole("button", { name: "确认入队" }).click();
  await expect(page.getByText(/已入队，来源与切入点已保留/)).toBeVisible();
  await expect.poll(async () => (await page.request.get(`/api/series/${series.id}/topics`).then(r => r.json())).page.total).toBe(2);
  await page.getByRole("button", { name: "← 返回选题库" }).click();
  await page.getByRole("button", { name: "全部", exact: true }).click();
  await expect(page.locator(".content-card")).toHaveCount(2);
  await expect(page.getByRole("heading", { name: "改过的候选一" })).toBeVisible();
  await expect(page.getByTestId(`library-research-${batch.id}-c1`)).toBeVisible();
  await page.reload();
  await expect(page.locator(".content-card")).toHaveCount(2);
  await expect(page.getByRole("button", { name: "挑选本批次" })).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("library-mobile.png"), fullPage: true });
  expect(await page.evaluate(() => document.body.scrollWidth)).toBeLessThanOrEqual(390);
  await page.route("**/topic-library?**", r => r.fulfill({ status: 503, json: { error: { message: "测试读取失败" } } }));
  await page.getByRole("button", { name: "待选", exact: true }).click();
  await expect(page.getByRole("button", { name: "重新读取选题库" })).toBeVisible();
  await page.unroute("**/topic-library?**");
  await page.getByRole("button", { name: "重新读取选题库" }).click();
  await expect(page.getByText("暂无待选建议。")).toBeVisible();
  // Deterministic pagination fixture; all writes/confirmation above used real isolated API.
  await page.route("**/topic-library?**", r => {
    const offset = Number(new URL(r.request().url()).searchParams.get("offset") || 0);
    return r.fulfill({ json: { items: Array.from({ length: offset ? 1 : 20 }, (_, i) => ({
      id: `page-${offset + i}`, title: `分页选题 ${offset + i + 1}`, selection_state: "pending",
      batch_id: batch.id, candidate_id: "c1", angle: "测试", rationale: "测试", stale: true, sources: [], available_actions: [],
    })), page: { total: 21, limit: 20, offset } } });
  });
  await page.getByRole("button", { name: "全部", exact: true }).click();
  // A just-visited filter can still be fresh in QueryClient's five-second cache.
  await page.getByRole("button", { name: "刷新选题库", exact: true }).click();
  await expect(page.locator(".content-card")).toHaveCount(20);
  await page.locator(".content-card").first().getByRole("button", { name: /查看选题/ }).click();
  const staleDetail = page.getByRole("dialog", { name: "选题详情" });
  await expect(staleDetail.getByRole("heading", { name: "分页选题 1" })).toBeVisible();
  await expect(staleDetail.getByRole("button", { name: "挑选本批次" })).toBeDisabled();
  await staleDetail.getByRole("button", { name: "关闭选题详情" }).click();
  await page.getByRole("button", { name: "下一页" }).click();
  await expect(page.getByRole("heading", { name: "分页选题 21" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "分页选题 21" })).toBeVisible();
  await page.getByRole("button", { name: "上一页" }).click();
  await expect(page.locator(".content-card")).toHaveCount(20);
});
