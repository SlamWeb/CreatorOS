import { expect, test, type APIRequestContext } from "@playwright/test";

async function post(request: APIRequestContext, path: string, data: object) {
  const response = await request.post(path, { data });
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}

async function fixture(request: APIRequestContext) {
  const suffix = crypto.randomUUID().slice(0, 8);
  const account = await post(request, "/api/creators", { display_name: `设置面板-${suffix}` });
  const series = await post(request, `/api/creators/${account.id}/series`, { name: `栏目设置-${suffix}` });
  await post(request, `/api/series/${series.id}/queue`, { request_id: crypto.randomUUID(),
    topics: [{ title: "作品保持原位" }, { title: "另一条选题" }] });
  return { account, series, suffix };
}

test("settings overlay preserves content position; rename persists and Escape restores focus", async ({ page, request }, info) => {
  const data = await fixture(request);
  await page.goto(`/?creator=${data.account.id}&series=${data.series.id}`);
  const card = page.getByRole("heading", { name: "作品保持原位", exact: true });
  await expect(card).toBeVisible();
  await expect(page.locator(".creator-task-summary")).toHaveCount(0);
  const origin = await card.boundingBox();
  const trigger = page.getByRole("button", { name: "栏目设置", exact: true });
  await trigger.click();
  const drawer = page.getByRole("dialog", { name: "栏目设置", exact: true });
  await expect(drawer).toBeVisible();
  await expect(drawer).toHaveCSS("transform", "none");
  const after = await card.boundingBox();
  expect(Math.abs(after!.x - origin!.x)).toBeLessThan(1);
  expect(Math.abs(after!.y - origin!.y)).toBeLessThan(1);
  const nameInput = drawer.getByLabel("栏目名称", { exact: true });
  const save = drawer.getByRole("button", { name: "保存名称", exact: true });
  const inputRect = await nameInput.boundingBox();
  const buttonRect = await save.boundingBox();
  expect(Math.abs(inputRect!.height - buttonRect!.height)).toBeLessThan(1);
  expect(Math.abs(inputRect!.y - buttonRect!.y)).toBeLessThan(1);
  const updated = `新栏目名称-${data.suffix}`;
  await nameInput.fill(updated);
  await save.click();
  await expect(drawer.getByText("名称已保存", { exact: true })).toBeVisible();
  expect(await request.get(`/api/series/${data.series.id}`).then(r => r.json())).toMatchObject({ name: updated, creator_id: data.account.id });
  await page.screenshot({ path: info.outputPath("series-settings-desktop.png"), fullPage: true });
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  await expect(trigger).toBeFocused();
  await expect(page.getByRole("heading", { name: updated, exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: updated, exact: true })).toBeVisible();
  await page.getByRole("button", { name: "添加选题", exact: true }).click();
  const addField = page.getByLabel("新选题标题", { exact: true });
  const addButton = page.getByRole("button", { name: "添加", exact: true });
  const addRect = await addField.boundingBox();
  const submitRect = await addButton.boundingBox();
  expect(Math.abs(addRect!.height - submitRect!.height)).toBeLessThan(1);
  expect(Math.abs(addRect!.y - submitRect!.y)).toBeLessThan(1);
  await addField.fill(`新选题-${data.suffix}`);
  await addButton.click();
  await expect(page.getByRole("heading", { name: `新选题-${data.suffix}`, exact: true })).toBeVisible();
  await page.setViewportSize({ width: 870, height: 1004 });
  const actionRects = await page.locator(".library-heading-actions > button").evaluateAll(elements => elements.map(element => {
    const rect = element.getBoundingClientRect(); return { top: rect.top, height: rect.height };
  }));
  expect(actionRects).toHaveLength(3);
  expect(new Set(actionRects.map(rect => rect.top)).size).toBe(1);
  expect(actionRects.every(rect => rect.height === 40)).toBe(true);
  await page.screenshot({ path: info.outputPath("series-toolbar-870.png"), fullPage: true });
  await trigger.click();
  await expect(drawer).toBeVisible();
  await expect(drawer).toHaveCSS("transform", "none");
  await page.screenshot({ path: info.outputPath("series-settings-870.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const bounds = await drawer.boundingBox();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
  await expect(drawer.getByRole("button", { name: "关闭栏目设置", exact: true })).toBeInViewport();
  await page.screenshot({ path: info.outputPath("series-settings-mobile.png"), fullPage: true });
  await drawer.getByRole("button", { name: "关闭栏目设置", exact: true }).click();
  await expect(drawer).toBeHidden();
  await expect(trigger).toBeFocused();
});

test("rename conflict retains its draft; ownership change still persists through the drawer", async ({ page, request }) => {
  const data = await fixture(request);
  const other = await post(request, "/api/creators", { display_name: `接收账号-${data.suffix}` });
  await page.goto(`/?creator=${data.account.id}&series=${data.series.id}`);
  await page.getByRole("button", { name: "栏目设置", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "栏目设置", exact: true });
  // Explicit controlled conflict; successful writes below use real isolated HTTP/SQLite.
  await page.route(`**/api/series/${data.series.id}`, route => route.request().method() === "PATCH"
    ? route.fulfill({ status: 409, json: { error: { message: "隔离冲突：栏目已变化" } } }) : route.continue());
  await drawer.getByLabel("栏目名称", { exact: true }).fill("保留失败后的名称");
  await drawer.getByRole("button", { name: "保存名称", exact: true }).click();
  await expect(drawer.getByRole("alert")).toContainText("栏目已变化");
  await expect(drawer.getByLabel("栏目名称", { exact: true })).toHaveValue("保留失败后的名称");
  expect(await request.get(`/api/series/${data.series.id}`).then(r => r.json())).toMatchObject({ name: data.series.name });
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "栏目设置", exact: true }).click();
  await expect(drawer.getByLabel("栏目名称", { exact: true })).toHaveValue("保留失败后的名称");
  await drawer.getByLabel("归属账号", { exact: true }).selectOption(other.id);
  await expect.poll(async () => (await request.get(`/api/series/${data.series.id}`).then(r => r.json())).creator_id).toBe(other.id);
  await expect.poll(() => new URL(page.url()).searchParams.get("creator")).toBe(other.id);
  await page.reload();
  await page.getByRole("button", { name: "栏目设置", exact: true }).click();
  await expect(drawer.getByLabel("归属账号", { exact: true })).toHaveValue(other.id);
});
