import { expect, test, type APIRequestContext } from "@playwright/test";

async function fixture(request: APIRequestContext) {
  const post = async (url: string, data: object) => {
    const response = await request.post(url, { data });
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  };
  const account = await post("/api/creators", { display_name: `最近内容-${crypto.randomUUID().slice(0, 6)}` });
  const series = await post(`/api/creators/${account.id}/series`, { name: "最近的故事" });
  const topics = await post(`/api/series/${series.id}/queue`, { request_id: crypto.randomUUID(),
    topics: Array.from({ length: 27 }, (_, i) => ({
      title: `故事选题 ${i + 1}`, brief: "用具体的场景讲一个有起伏的小故事，保留角色的行动与结尾。".repeat(3),
    })) });
  const newest = await post(`/api/series/${series.id}/queue`, { request_id: crypto.randomUUID(),
    topics: [{ title: "刚添加的新选题", brief: "应该直接出现在前面，不必往下翻。" }] });
  // Existing isolated E2EProducer writes validated files; no paid model or publishing.
  const run = await post("/api/runs", { topic_id: topics.topic_ids[0] });
  await post(`/api/runs/${run.id}/execute`, { expected_version: run.version });
  await expect.poll(async () => (await request.get(`/api/runs/${run.id}`).then(r => r.json())).status).toBe("awaiting_approval");
  return { account, series, topics, newest, run };
}

test("recent content is globally first; account previews and top pagination avoid a long scroll", async ({ page, request }, info) => {
  const data = await fixture(request);
  const original = await request.get(`/api/series/${data.series.id}/topics?limit=100`).then(r => r.json());
  const writes: string[] = [];
  page.on("request", req => { if (req.method() !== "GET") writes.push(req.url()); });
  await page.goto(`/?creator=${data.account.id}&topics=queued`);
  await expect(page.locator(".content-card")).toHaveCount(6);
  await expect(page.locator(".content-card").first()).toContainText("故事选题 1");
  await expect(page.locator(".content-card").nth(1)).toContainText("刚添加的新选题");
  const cards = await page.locator(".content-card").evaluateAll(items => items.slice(0, 2).map(item => {
    const rect = item.getBoundingClientRect(); return { x: rect.x, y: rect.y };
  }));
  expect(Math.abs(cards[0].y - cards[1].y)).toBeLessThan(2);
  expect(cards[1].x).toBeGreaterThan(cards[0].x);
  await page.screenshot({ path: info.outputPath("account-preview-desktop.png"), fullPage: true });
  await page.getByRole("button", { name: "查看全部", exact: true }).click();
  await expect(page).toHaveURL(/topics=queued/);
  await expect(page.locator(".content-card")).toHaveCount(20);
  const top = page.getByRole("navigation", { name: "选题分页 · 顶部" });
  await expect(top).toBeInViewport();
  await top.getByRole("button", { name: "下一页" }).click();
  await expect(page.locator(".content-card")).toHaveCount(8);
  await expect(page).toHaveURL(/offset=20/);
  await expect(top).toBeInViewport();
  await page.reload();
  await expect(page.getByLabel("选题页码 · 顶部")).toHaveValue("1");
  await expect(page.locator(".content-card")).toHaveCount(8);
  await page.getByLabel("内容排序").selectOption("queue");
  await expect(page).not.toHaveURL(/offset=/);
  await expect(page.locator(".content-card").first()).toContainText("故事选题 1");
  await page.getByLabel("选题页码 · 顶部").selectOption("1");
  await expect(page.locator(".content-card").last()).toContainText("刚添加的新选题");
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await page.getByRole("navigation", { name: "选题分页 · 底部" }).getByRole("button", { name: "上一页" }).click();
  await expect(top).toBeInViewport();
  await page.getByLabel("内容排序").selectOption("recent");
  await expect(page.locator(".content-card").nth(1)).toContainText("刚添加的新选题");
  await page.screenshot({ path: info.outputPath("series-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(top).toBeInViewport();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("series-mobile.png"), fullPage: false });
  await top.getByRole("button", { name: "下一页" }).click();
  await expect(page.locator(".content-card")).toHaveCount(8);
  await expect(top).toBeInViewport();
  expect(writes).toEqual([]);
  expect(await request.get(`/api/series/${data.series.id}/topics?limit=100`).then(r => r.json())).toEqual(original);
});

test("failed page stays explicit; retry does not write or confuse recent and queue order", async ({ page, request }) => {
  const data = await fixture(request);
  await page.goto(`/?creator=${data.account.id}&series=${data.series.id}&topics=all`);
  await expect(page.locator(".content-card")).toHaveCount(20);
  await page.route("**/topic-library?**", async route => {
    if (new URL(route.request().url()).searchParams.get("offset") === "20") {
      await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "隔离测试：列表暂不可读取" }) });
    } else await route.continue();
  });
  await page.getByRole("navigation", { name: "选题分页 · 顶部" }).getByRole("button", { name: "下一页" }).click();
  await expect(page.getByRole("alert")).toContainText("列表暂不可读取");
  await expect(page.getByText("还没有选题。", { exact: true })).toHaveCount(0);
  await page.unroute("**/topic-library?**");
  await page.getByRole("button", { name: "重新读取选题库" }).click();
  await expect(page.locator(".content-card")).toHaveCount(8);
  await page.getByRole("button", { name: "已生产", exact: true }).click();
  await expect(page).not.toHaveURL(/offset=/);
  await expect(page.locator(".content-card")).toHaveCount(1);
  await expect(page.locator(".content-card")).toContainText("故事选题 1");
  await page.getByRole("button", { name: "全部", exact: true }).click();
  await expect(page.locator(".content-card").nth(1)).toContainText("刚添加的新选题");
});
