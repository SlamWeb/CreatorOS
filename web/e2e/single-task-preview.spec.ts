import { expect, test, type APIRequestContext } from "@playwright/test";

async function post(request: APIRequestContext, url: string, data: object = {}) {
  const response = await request.post(url, { data });
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}

// Real isolated HTTP/SQLite/artifacts; local E2EProducer, no paid generation.
test("single task previews its current image directly; multiple and waiting tasks keep details", async ({ page, request }, info) => {
  const account = await post(request, "/api/creators", { display_name: `卡片测试-${crypto.randomUUID().slice(0, 6)}` });
  const series = (await post(request, "/api/series", { creator_id: account.id, name: "卡片预览", skill_name: "knowledge-to-carousel", request_id: crypto.randomUUID() })).series;
  const queued = await post(request, `/api/series/${series.id}/queue`, { request_id: crypto.randomUUID(), topics: [{ title: "一项制作直接看图" }, { title: "两项制作打开详情" }] });
  const results = [];
  for (let index = 0; index < 2; index++) results.push(await post(request, `/api/topics/${queued.topic_ids[index]}/production-tasks`, {
    request_id: crypto.randomUUID(), tasks: Array.from({ length: index + 1 }, (_, n) => ({ title: `制作 ${n + 1}`, brief: "隔离卡片测试" })),
  }));
  const libraryUrl = `/api/series/${series.id}/topic-library`;
  await expect.poll(async () => {
    const rows = await request.get(libraryUrl).then(r => r.json());
    return rows.items.find((row: { id: string }) => row.id === queued.topic_ids[0])?.preview_run_status;
  }).toBe("awaiting_approval");
  const url = `/?creator=${account.id}&series=${series.id}`;
  const writes: string[] = [];
  page.on("request", r => { if (!["GET", "HEAD"].includes(r.method())) writes.push(r.url()); });
  await page.goto(url);
  const single = page.getByTestId(`library-${queued.topic_ids[0]}`);
  await expect(single.locator("img")).toBeVisible();
  await expect.poll(() => single.locator("img").evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
  await expect(single).toContainText("待批准");
  await single.getByRole("link", { name: /查看内容/ }).click();
  await expect(page).toHaveURL(new RegExp(`/runs/${results[0].items[0].run_id}`));
  await page.getByRole("link", { name: "返回栏目", exact: true }).click();
  await expect(single.locator("img")).toBeVisible();
  await page.reload();
  await expect(single.locator("img")).toBeVisible();
  const multiple = page.getByTestId(`library-${queued.topic_ids[1]}`);
  await expect(multiple.getByRole("link", { name: /查看内容/ })).toHaveCount(0);
  await multiple.getByRole("button", { name: /查看选题/ }).click();
  await expect(page.getByRole("dialog", { name: "选题详情" }).locator(".production-task-list > li")).toHaveCount(2);
  await page.keyboard.press("Escape");
  await page.screenshot({ path: info.outputPath("single-task-preview.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await single.scrollIntoViewIfNeeded();
  await expect(single.locator("img")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("single-task-preview-mobile.png"), fullPage: true });
  expect(writes).toEqual([]);
  const waiting = await post(request, "/test/waiting-task");
  await page.goto(`/?creator=${waiting.creator_id}&series=${waiting.series_id}`);
  const waitingCard = page.getByTestId(`library-${waiting.topic_id}`);
  await expect(waitingCard).toContainText("2 项制作");
  await expect(waitingCard.getByRole("link", { name: /查看内容/ })).toHaveCount(0);
  await waitingCard.getByRole("button", { name: /查看选题/ }).click();
  await expect(page.getByRole("dialog", { name: "选题详情" })).toContainText("等待图片完成后剪辑");
});
