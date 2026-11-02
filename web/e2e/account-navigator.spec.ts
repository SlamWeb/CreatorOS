import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// All successful reads/writes use playwright.config.ts's temporary SQLite/files
// and isolated E2E server (8877). The only intercepted write is the explicit 409.
async function post(request: APIRequestContext, url: string, data: object) {
  const response = await request.post(url, { data });
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}

async function accountsFixture(request: APIRequestContext) {
  const suffix = crypto.randomUUID().slice(0, 8);
  const account = await post(request, "/api/creators", { display_name: `导航甲-${suffix}` });
  const other = await post(request, "/api/creators", { display_name: `导航乙-${suffix}` });
  const first = await post(request, `/api/creators/${account.id}/series`, { name: `甲的栏目-${suffix}` });
  const second = await post(request, `/api/creators/${account.id}/series`, { name: `甲的长栏目名称用于检查换行与当前账号归属-${suffix}` });
  const external = await post(request, `/api/creators/${other.id}/series`, { name: `乙的栏目-${suffix}` });
  return { suffix, account, other, first, second, external };
}

async function openRail(page: Page) {
  const rail = page.getByRole("complementary", { name: "账号与栏目", includeHidden: true });
  // Initial HTTP loading has no rail yet; do not mistake that for a collapsed
  // mobile rail and wait for the desktop-hidden toggle instead.
  await expect(rail).toBeAttached();
  if (!await rail.isVisible()) await page.getByRole("button", { name: /^账号与栏目 ·/ }).click();
  await expect(rail).toBeVisible();
  return rail;
}

async function openSwitcher(page: Page) {
  await (await openRail(page)).getByRole("button", { name: "切换账号", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "切换账号", exact: true });
  await expect(dialog).toBeVisible();
  return dialog;
}

async function expectLocation(page: Page, creator: string, series: string | null = null) {
  await expect.poll(() => {
    const params = new URL(page.url()).searchParams;
    return { creator: params.get("creator"), series: params.get("series") };
  }).toEqual({ creator, series });
}

test("searchable account switching scopes the rail and survives refresh and browser back", async ({ page, request }) => {
  const data = await accountsFixture(request);
  await page.goto(`/?creator=${data.account.id}&series=${data.first.id}`);
  const rail = await openRail(page);
  await expect(rail.getByRole("button", { name: data.first.name, exact: true })).toBeVisible();
  await expect(rail.getByRole("button", { name: data.second.name, exact: true })).toBeVisible();
  await expect(rail.getByRole("button", { name: data.external.name, exact: true })).toHaveCount(0);

  const switcher = await openSwitcher(page);
  await switcher.getByLabel("搜索账号", { exact: true }).fill(data.other.display_name);
  await expect(switcher.getByRole("button", { name: `切换到账号 ${data.other.display_name}`, exact: true })).toBeVisible();
  await expect(switcher.getByRole("button", { name: `切换到账号 ${data.account.display_name}`, exact: true })).toHaveCount(0);
  await expect(switcher.getByRole("button", { name: "新建账号", exact: true })).toBeVisible();
  await switcher.getByRole("button", { name: `切换到账号 ${data.other.display_name}`, exact: true }).click();
  await expect(switcher).toBeHidden();
  await expectLocation(page, data.other.id);
  await expect(rail.getByRole("button", { name: data.external.name, exact: true })).toBeVisible();
  await expect(rail.getByRole("button", { name: data.first.name, exact: true })).toHaveCount(0);
  await expect(rail.getByRole("button", { name: "新建栏目", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "栏目", exact: true })).toContainText(data.external.name);
  await page.reload();
  await expectLocation(page, data.other.id);
  await expect(rail.getByRole("button", { name: data.external.name, exact: true })).toBeVisible();
  await page.goBack();
  await expectLocation(page, data.account.id, data.first.id);
  await expect(page.getByRole("heading", { name: data.first.name, exact: true, level: 1 })).toBeVisible();
  await rail.getByRole("button", { name: "账号概览", exact: true }).click();
  await expectLocation(page, data.account.id);
  const columns = page.getByRole("region", { name: "栏目", exact: true });
  await expect(columns).toContainText(data.first.name);
  await expect(columns).toContainText(data.second.name);
  await expect(columns).not.toContainText(data.external.name);
});

test("creating a column persists its current owner; controlled conflicts retain the draft and cancel restores focus", async ({ page, request }) => {
  const data = await accountsFixture(request);
  await page.goto(`/?creator=${data.other.id}`);
  const rail = await openRail(page);
  const createButton = rail.getByRole("button", { name: "新建栏目", exact: true });
  await createButton.click();
  const dialog = page.getByRole("dialog", { name: "新建栏目", exact: true });
  await expect(dialog).toContainText(data.other.display_name);
  const name = `从当前账号创建-${data.suffix}`;
  await dialog.getByLabel("新栏目名称", { exact: true }).fill(name);
  const created = page.waitForResponse(response => response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/series");
  await dialog.getByRole("button", { name: "创建", exact: true }).click();
  const response = await created;
  expect(response.ok(), await response.text()).toBeTruthy();
  const result = await response.json();
  await expect(dialog).toBeHidden();
  await expectLocation(page, data.other.id, result.series.id);
  const stored = await request.get(`/api/series/${result.series.id}`).then(reply => reply.json());
  expect(stored).toMatchObject({ name, creator_id: data.other.id });
  await page.reload();
  await expect(page.getByRole("heading", { name, level: 1, exact: true })).toBeVisible();
  await expect(rail.getByRole("button", { name, exact: true })).toBeVisible();

  // Controlled failure injection only: success above uses the real isolated API.
  let attempts = 0;
  await page.route("**/api/series", route => {
    if (route.request().method() !== "POST") return route.continue();
    attempts++;
    return route.fulfill({ status: 409, json: { error: { message: "隔离冲突：栏目暂不可创建" } } });
  });
  const beforeFailure = page.url();
  await createButton.click();
  await dialog.getByLabel("新栏目名称", { exact: true }).fill("失败后保留的栏目草稿");
  await dialog.getByRole("button", { name: "创建", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("栏目暂不可创建");
  await expect(dialog.getByLabel("新栏目名称", { exact: true })).toHaveValue("失败后保留的栏目草稿");
  await expect(page).toHaveURL(beforeFailure);
  expect(attempts).toBe(1);
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(createButton).toBeFocused();
  await createButton.click();
  await expect(dialog.getByRole("alert")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(createButton).toBeFocused();
  expect(attempts).toBe(1);
});

test("creating an account selects the persisted empty account and keeps both creation entries reachable", async ({ page, request }, info) => {
  const data = await accountsFixture(request);
  await page.goto(`/?creator=${data.account.id}`);
  const switcher = await openSwitcher(page);
  await switcher.getByRole("button", { name: "新建账号", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "新建账号", exact: true });
  await expect(dialog).toBeVisible();
  const name = `新建空账号-${data.suffix}`;
  const handle = `nav-${data.suffix}`;
  await dialog.getByLabel("新账号名称", { exact: true }).fill(name);
  await dialog.getByLabel("新账号标识", { exact: true }).fill(handle);
  const created = page.waitForResponse(response => response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/creators");
  await dialog.getByRole("button", { name: "创建", exact: true }).click();
  const response = await created;
  expect(response.ok(), await response.text()).toBeTruthy();
  const account = await response.json();
  await expect(dialog).toBeHidden();
  await expectLocation(page, account.id);
  expect(await request.get(`/api/creators/${account.id}`).then(reply => reply.json()))
    .toMatchObject({ display_name: name, account_handle: handle });
  await page.reload();
  await expectLocation(page, account.id);
  const columns = page.getByRole("region", { name: "栏目", exact: true });
  const recent = page.getByRole("region", { name: "近期作品", exact: true });
  await expect(columns).toBeVisible();
  await expect(columns).toContainText(/还没有|暂无/);
  await expect(recent).toContainText(/还没有|暂无/);
  await expect(recent.getByRole("img")).toHaveCount(0);
  await expect(columns).not.toContainText(data.first.name);
  await expect((await openRail(page)).getByRole("button", { name: "新建栏目", exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath("account-navigator-empty-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  const mobileSwitcher = await openSwitcher(page);
  await expect(mobileSwitcher.getByRole("button", { name: "新建账号", exact: true })).toBeVisible();
  const switcherBounds = await mobileSwitcher.boundingBox();
  expect(switcherBounds).not.toBeNull();
  expect(switcherBounds!.x).toBeGreaterThanOrEqual(0);
  expect(switcherBounds!.x + switcherBounds!.width).toBeLessThanOrEqual(390);
  await expect(mobileSwitcher.getByLabel("搜索账号", { exact: true })).toBeInViewport();
  await page.screenshot({ path: info.outputPath("account-navigator-switcher-mobile.png"), fullPage: true });
  await page.keyboard.press("Escape");
  await expect(mobileSwitcher).toBeHidden();
  await expect(page.getByRole("button", { name: "切换账号", exact: true })).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("account overview lists all its columns and bounds recent image works at desktop and 390px", async ({ page, request }, info) => {
  const data = await accountsFixture(request);
  // Use the declared image-producing built-in, as ui-foundation.spec.ts does;
  // inspector fixtures intentionally have no image-output capability metadata.
  await post(request, "/test/calibration", {});
  const catalogResponse = await request.get("/api/producer-skills?include_versions=true");
  expect(catalogResponse.ok(), await catalogResponse.text()).toBeTruthy();
  const catalog = await catalogResponse.json();
  const skill = catalog.items.find((item: { name: string }) => item.name === "knowledge-to-carousel");
  expect(skill).toBeTruthy();
  const queued = await post(request, `/api/series/${data.first.id}/queue`, {
    request_id: crypto.randomUUID(),
    topics: [
      ...Array.from({ length: 7 }, (_, index) => ({ title: `近期有图作品-${data.suffix}-${index + 1}` })),
      { title: `未生产草稿不应进入近期作品-${data.suffix}` },
    ],
  });
  // E2EProducer generates local validated PNGs; these requests never call a model.
  const runs: { id: string; version: number }[] = [];
  for (const topicId of queued.topic_ids.slice(0, 7)) {
    const run = await post(request, "/api/runs", {
      topic_id: topicId, request_id: crypto.randomUUID(),
      skill_overrides: { skill_id: skill.id, expected_digests: { [skill.id]: skill.digest } },
    });
    runs.push(run);
    await post(request, `/api/runs/${run.id}/execute`, { expected_version: run.version });
  }
  await expect.poll(async () => Promise.all(runs.map(run => request.get(`/api/runs/${run.id}`)
    .then(response => response.json()).then(result => result.status))), { timeout: 30_000 })
    .toEqual(Array(7).fill("awaiting_approval"));
  await page.goto(`/?creator=${data.account.id}`);
  const columns = page.getByRole("region", { name: "栏目", exact: true });
  await expect(columns).toContainText(data.first.name);
  await expect(columns).toContainText(data.second.name);
  await expect(columns).not.toContainText(data.external.name);
  const recent = page.getByRole("region", { name: "近期作品", exact: true });
  await expect(recent.getByRole("img")).toHaveCount(6);
  await expect(recent).not.toContainText(`未生产草稿不应进入近期作品-${data.suffix}`);
  for (const image of await recent.getByRole("img").all()) {
    // Covers are lazy-loaded; explicitly bring each one into view before
    // asserting its decoded file dimensions, including below-fold cards.
    await image.scrollIntoViewIfNeeded();
    await expect.poll(() => image.evaluate((element: HTMLImageElement) => element.naturalWidth)).toBe(720);
  }
  await page.screenshot({ path: info.outputPath("account-navigator-overview-desktop.png"), fullPage: true });
  const contentLink = recent.locator('a[href*="/runs/"]').first();
  const href = await contentLink.getAttribute("href");
  expect(href).not.toBeNull();
  const destination = new URL(href!, page.url());
  const runIndex = runs.findIndex(run => destination.pathname === `/runs/${run.id}`);
  expect(runIndex).toBeGreaterThanOrEqual(0);
  const chatTurns: string[] = [];
  page.on("request", event => {
    if (event.method() === "POST" && /\/api\/agent\/sessions\/[^/]+\/turns$/.test(new URL(event.url()).pathname)) {
      chatTurns.push(event.url());
    }
  });
  await page.getByRole("button", { name: "打开账号对话", exact: true }).click();
  const chatPanel = page.locator(".account-chat-panel");
  await expect(chatPanel).toBeVisible();
  await expect(chatPanel.getByRole("heading", { name: data.account.display_name, exact: true })).toBeVisible();
  await contentLink.click();
  await expect(page.getByRole("heading", { name: `近期有图作品-${data.suffix}-${runIndex + 1}`, exact: true })).toBeVisible();
  await expect(chatPanel).toBeVisible();
  await expect(chatPanel.getByRole("heading", { name: data.account.display_name, exact: true })).toBeVisible();
  await page.getByRole("link", { name: "返回栏目", exact: true }).click();
  await expectLocation(page, data.account.id);
  await expect(recent.getByRole("img")).toHaveCount(6);
  await expect(chatPanel.getByRole("heading", { name: data.account.display_name, exact: true })).toBeVisible();
  await chatPanel.getByRole("button", { name: "关闭账号对话", exact: true }).click();
  await expect(chatPanel).toBeHidden();
  expect(chatTurns).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(columns).toContainText(data.second.name);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("account-navigator-overview-mobile.png"), fullPage: true });
  const rail = await openRail(page);
  await expect(rail.getByRole("button", { name: data.first.name, exact: true })).toBeVisible();
  await expect(rail.getByRole("button", { name: "新建栏目", exact: true })).toBeInViewport();
  await page.screenshot({ path: info.outputPath("account-navigator-rail-mobile.png"), fullPage: true });
});
