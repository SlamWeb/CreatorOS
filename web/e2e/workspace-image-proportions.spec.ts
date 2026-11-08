import { expect, test } from "@playwright/test";

test("workspace covers preserve the real image ratio and show missing-image feedback", async ({ page, request }, info) => {
  const creatorResponse = await request.post("/api/creators", { data: { display_name: `图片比例-${crypto.randomUUID().slice(0, 8)}` } });
  expect(creatorResponse.ok()).toBeTruthy();
  const creator = await creatorResponse.json();
  const seriesResponse = await request.post(`/api/creators/${creator.id}/series`, { data: { name: "图片比例栏目" } });
  expect(seriesResponse.ok()).toBeTruthy();
  const series = await seriesResponse.json();
  const queue = await request.post(`/api/series/${series.id}/queue`, { data: { request_id: crypto.randomUUID(), topics: [
    { title: "正方形图片" }, { title: "横向图片" }, { title: "竖版图片" }, { title: "丢失图片" },
  ] } });
  expect(queue.ok()).toBeTruthy();
  const { topic_ids: topicIds } = await queue.json();
  const images = ["square", "wide", "portrait", "missing"];
  await page.route("**/api/series/*/topic-library?**", async route => {
    const response = await route.fetch();
    const data = await response.json();
    data.items = data.items.map((item: { id: string }) => {
      const index = topicIds.indexOf(item.id);
      return index < 0 ? item : { ...item, cover_url: `/api/image-proportion-${images[index]}` };
    });
    await route.fulfill({ response, json: data });
  });
  await page.route("**/api/image-proportion-*", async route => {
    const id = route.request().url().split("-").at(-1);
    if (id === "missing") return route.fulfill({ status: 404, body: "missing" });
    const [width, height] = id === "square" ? [400, 400] : id === "portrait" ? [300, 400] : [600, 300];
    return route.fulfill({ contentType: "image/svg+xml", body: `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><rect width="100%" height="100%" fill="#a9c2d8"/></svg>` });
  });
  await page.goto(`/workspace?creator=${creator.id}&series=${series.id}`);
  const square = page.getByRole("img", { name: "正方形图片 · 第一张图片" });
  const wide = page.getByRole("img", { name: "横向图片 · 第一张图片" });
  const portrait = page.getByRole("img", { name: "竖版图片 · 第一张图片" });
  await expect(square).toBeVisible({ timeout: 15000 });
  await expect.poll(() => square.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBe(400);
  await expect.poll(() => wide.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBe(600);
  await expect.poll(() => portrait.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBe(300);
  for (const [image, ratio] of [[square, 1], [wide, 2], [portrait, 0.75]] as const) {
    const box = await image.boundingBox();
    expect(box).toBeTruthy();
    expect(Math.abs(box!.width / box!.height - ratio)).toBeLessThan(0.02);
    await expect(image).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
  }
  await expect(page.getByText("封面暂不可读取")).toBeVisible();
  await page.screenshot({ path: info.outputPath("uncropped-column-covers-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  const mobileSquare = await square.boundingBox();
  expect(mobileSquare).toBeTruthy();
  expect(Math.abs(mobileSquare!.width / mobileSquare!.height - 1)).toBeLessThan(0.02);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("uncropped-column-covers-mobile.png"), fullPage: true });
});

test("recent works keep their natural ratio, including while hovered", async ({ page, request }, info) => {
  const created = await request.post("/api/creators", { data: { display_name: `近期图片-${crypto.randomUUID().slice(0, 8)}` } });
  expect(created.ok()).toBeTruthy();
  const creator = await created.json();
  const createdSeries = await request.post(`/api/creators/${creator.id}/series`, { data: { name: "图片栏目" } });
  expect(createdSeries.ok()).toBeTruthy();
  const series = await createdSeries.json();
  await page.route("**/api/runs?**", route => route.fulfill({ json: { items: [
    { id: "square-run", topic_id: "square-topic", creator_id: creator.id, series_id: series.id,
      cover_url: "/api/image-proportion-square", updated_at: "2026-11-08T00:00:00Z", topic_title: "正方形近期作品",
      series_name: series.name, status: "awaiting_approval", card_count: 1 },
    { id: "wide-run", topic_id: "wide-topic", creator_id: creator.id, series_id: series.id,
      cover_url: "/api/image-proportion-wide", updated_at: "2026-11-07T00:00:00Z", topic_title: "横向近期作品",
      series_name: series.name, status: "awaiting_approval", card_count: 1 },
  ], page: { total: 2, offset: 0, limit: 50 } } }));
  await page.route("**/api/image-proportion-*", route => {
    const square = route.request().url().endsWith("square");
    const [width, height] = square ? [400, 400] : [600, 300];
    return route.fulfill({ contentType: "image/svg+xml", body: `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><rect width="100%" height="100%" fill="#a9c2d8"/></svg>` });
  });
  await page.goto(`/workspace?creator=${creator.id}`);
  const recent = page.getByRole("region", { name: "近期作品", exact: true });
  const square = recent.getByRole("img", { name: "正方形近期作品" });
  const wide = recent.getByRole("img", { name: "横向近期作品" });
  await expect(square).toBeVisible({ timeout: 15000 });
  await expect.poll(() => square.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBe(400);
  await expect.poll(() => wide.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBe(600);
  for (const [image, ratio] of [[square, 1], [wide, 2]] as const) {
    const box = await image.boundingBox();
    expect(box).toBeTruthy();
    expect(Math.abs(box!.width / box!.height - ratio)).toBeLessThan(0.02);
    await image.hover();
    await expect(image).toHaveCSS("transform", "none");
  }
  await page.screenshot({ path: info.outputPath("uncropped-recent-covers.png"), fullPage: true });
});
