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

test("a late assignment response keeps the user's newer workspace location", async ({ page, request }) => {
  const data = await fixture(request);
  const other = await post(request, "/api/creators", { display_name: `并发接收账号-${data.suffix}` });
  await page.goto(`/?creator=${data.account.id}&view=data`);
  await page.getByRole("button", { name: data.series.name, exact: true }).first().click();
  await expect(page).toHaveURL(url => url.searchParams.get("series") === data.series.id);
  await page.getByRole("button", { name: "栏目设置", exact: true }).click();

  let release!: () => void;
  let captured!: () => void;
  const hold = new Promise<void>(resolve => { release = resolve; });
  const responseReady = new Promise<void>(resolve => { captured = resolve; });
  await page.route(`**/api/series/${data.series.id}/assignment`, async route => {
    const response = await route.fetch(); // real isolated SQLite write; only delivery is delayed
    captured();
    await hold;
    await route.fulfill({ response });
  });
  await page.getByRole("dialog", { name: "栏目设置" }).getByLabel("归属账号").selectOption(other.id);
  await responseReady;
  await page.goBack();
  await expect(page).toHaveURL(url => url.searchParams.get("creator") === data.account.id && !url.searchParams.has("series"));
  const refreshed = page.waitForResponse(response => response.request().method() === "GET" && new URL(response.url()).pathname === "/api/series");
  release();
  await refreshed;
  await page.waitForLoadState("networkidle");
  await expect.poll(async () => (await request.get(`/api/series/${data.series.id}`).then(r => r.json())).creator_id).toBe(other.id);
  await expect(page.getByRole("heading", { name: data.account.display_name, exact: true, level: 1 })).toBeVisible();
  await expect(page).toHaveURL(url => url.searchParams.get("creator") === data.account.id && !url.searchParams.has("series"));
});

test("a pending assignment survives leaving and returning; its late failure is visible", async ({ page, request }) => {
  const data = await fixture(request);
  const other = await post(request, "/api/creators", { display_name: `迟到失败账号-${data.suffix}` });
  await page.goto(`/?creator=${data.account.id}&view=data`);
  await page.getByRole("button", { name: data.series.name, exact: true }).first().click();
  await page.getByRole("button", { name: "栏目设置", exact: true }).click();
  let release!: () => void;
  let captured!: () => void;
  const hold = new Promise<void>(resolve => { release = resolve; });
  const ready = new Promise<void>(resolve => { captured = resolve; });
  let writes = 0;
  await page.route(`**/api/series/${data.series.id}/assignment`, async route => {
    writes++;
    captured();
    await hold;
    await route.fulfill({ status: 409, json: { error: { message: "受控迟到归属冲突" } } });
  });
  await page.getByRole("dialog", { name: "栏目设置" }).getByLabel("归属账号").selectOption(other.id);
  await ready;
  await page.goBack();
  await page.goForward();
  await page.getByRole("button", { name: "栏目设置", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "栏目设置" });
  await expect(drawer.getByText("正在调整归属…")).toBeVisible();
  await expect(drawer.getByLabel("归属账号")).toBeDisabled();
  await expect(drawer.getByRole("button", { name: `删除栏目 ${data.series.name}` })).toBeDisabled();
  expect(writes).toBe(1);
  release();
  await expect(drawer.getByRole("alert")).toContainText("受控迟到归属冲突");
  await expect(drawer.getByLabel("归属账号")).toBeEnabled();
  expect(writes).toBe(1);
  expect(await request.get(`/api/series/${data.series.id}`).then(r => r.json())).toMatchObject({ creator_id: data.account.id });
});

test("a pending removal survives leaving and returning; its late failure is visible", async ({ page, request }) => {
  const data = await fixture(request);
  await page.goto(`/?creator=${data.account.id}&view=data`);
  await page.getByRole("button", { name: data.series.name, exact: true }).first().click();
  await page.getByRole("button", { name: "栏目设置", exact: true }).click();
  let release!: () => void;
  let captured!: () => void;
  const hold = new Promise<void>(resolve => { release = resolve; });
  const ready = new Promise<void>(resolve => { captured = resolve; });
  let writes = 0;
  await page.route(`**/api/series/${data.series.id}`, async route => {
    if (route.request().method() !== "DELETE") return route.continue();
    writes++;
    captured();
    await hold;
    await route.fulfill({ status: 409, json: { error: { message: "受控迟到删除冲突" } } });
  });
  page.once("dialog", dialog => dialog.accept());
  await page.getByRole("dialog", { name: "栏目设置" }).getByRole("button", { name: `删除栏目 ${data.series.name}` }).click();
  await ready;
  await page.goBack();
  await page.goForward();
  await page.getByRole("button", { name: "栏目设置", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "栏目设置" });
  await expect(drawer.getByRole("button", { name: `删除栏目 ${data.series.name}` })).toBeDisabled();
  await expect(drawer.getByLabel("归属账号")).toBeDisabled();
  expect(writes).toBe(1);
  release();
  await expect(drawer.getByRole("alert")).toContainText("受控迟到删除冲突");
  expect(writes).toBe(1);
  expect((await request.get(`/api/series/${data.series.id}`)).ok()).toBe(true);
});

test("a successful assignment finishes in the workspace revisited across routes", async ({ page, request }) => {
  const data = await fixture(request);
  const other = await post(request, "/api/creators", { display_name: `跨页面接收账号-${data.suffix}` });
  await page.goto(`/?creator=${data.account.id}&view=data`);
  await page.getByRole("button", { name: data.series.name, exact: true }).first().click();
  await page.getByRole("button", { name: "栏目设置", exact: true }).click();
  let release!: () => void;
  let captured!: () => void;
  const hold = new Promise<void>(resolve => { release = resolve; });
  const ready = new Promise<void>(resolve => { captured = resolve; });
  await page.route(`**/api/series/${data.series.id}/assignment`, async route => {
    captured();
    await hold;
    await route.fulfill({ response: await route.fetch() });
  });
  await page.getByRole("dialog", { name: "栏目设置" }).getByLabel("归属账号").selectOption(other.id);
  await ready;
  await page.goBack();
  await page.getByRole("link", { name: "Skill", exact: true }).click();
  await expect(page).toHaveURL(/\/skills$/);
  await page.goBack();
  await page.getByRole("button", { name: data.series.name, exact: true }).first().click();
  await page.getByRole("button", { name: "栏目设置", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "栏目设置" }).getByLabel("归属账号")).toBeDisabled();
  release();
  await expect(page).toHaveURL(url => url.searchParams.get("creator") === other.id && url.searchParams.get("series") === data.series.id);
  expect(await request.get(`/api/series/${data.series.id}`).then(r => r.json())).toMatchObject({ creator_id: other.id });
});

test("column title and actions remain separate with the chat drawer open", async ({ page, request }, info) => {
  const account = await post(request, "/api/creators", { display_name: `布局账号-${crypto.randomUUID().slice(0, 8)}` });
  const series = await post(request, `/api/creators/${account.id}/series`, { name: "一个较长的栏目标题需要完整显示并且不能被按钮遮挡" });
  await page.goto(`/?creator=${account.id}&series=${series.id}`);
  const title = page.getByRole("heading", { name: series.name, exact: true, level: 1 });
  const header = page.locator(".series-content-section .library-heading");
  await expect(title).toBeVisible();
  const expectSeparate = async () => {
    const geometry = await header.evaluate(element => {
      const titleBox = element.querySelector(".workspace-title-group")!.getBoundingClientRect();
      return Array.from(element.querySelectorAll(".library-heading-actions > button"), button => {
        const box = button.getBoundingClientRect();
        return { overlap: titleBox.left < box.right - 1 && titleBox.right > box.left + 1
          && titleBox.top < box.bottom - 1 && titleBox.bottom > box.top + 1,
        outside: box.left < -1 || box.right > window.innerWidth + 1 };
      });
    });
    expect(geometry).toHaveLength(3);
    expect(geometry.every(item => !item.overlap && !item.outside)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  };
  await expectSeparate();
  await page.screenshot({ path: info.outputPath("title-desktop-closed.png") });
  await page.getByRole("button", { name: "打开账号对话" }).click();
  await expect(page.locator(".account-chat-panel")).toBeVisible();
  await page.screenshot({ path: info.outputPath("title-desktop-chat.png") });
  await expectSeparate();
  await page.getByRole("button", { name: "关闭账号对话" }).click();
  await page.setViewportSize({ width: 870, height: 1004 });
  await expectSeparate();
  await page.screenshot({ path: info.outputPath("title-narrow.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await expectSeparate();
  await page.screenshot({ path: info.outputPath("title-mobile-closed.png") });
  await page.getByRole("button", { name: "打开账号对话" }).click();
  await expect(page.locator(".account-chat-panel")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("title-mobile-chat.png") });
  await page.getByRole("button", { name: "关闭账号对话" }).click();
  await expect(title).toBeVisible();
  await expectSeparate();
});
