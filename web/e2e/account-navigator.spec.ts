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

test("account links and search work beyond the first 100 accounts", async ({ page, request }) => {
  test.setTimeout(180_000);
  const suffix = crypto.randomUUID().slice(0, 8);
  for (let index = 0; index < 101; index++) {
    await post(request, "/api/creators", { display_name: `目录填充-${suffix}-${index}` });
  }
  const target = await post(request, "/api/creators", { display_name: `第二区账号甲-${suffix}` });
  const other = await post(request, "/api/creators", { display_name: `第二区账号乙-${suffix}` });
  const targetSeries = await post(request, `/api/creators/${target.id}/series`, { name: `第二区栏目甲-${suffix}` });
  const otherSeries = await post(request, `/api/creators/${other.id}/series`, { name: `第二区栏目乙-${suffix}` });
  const first = await request.get("/api/creators?limit=100&offset=0").then(response => response.json());
  expect(first.page.total).toBeGreaterThan(100);
  expect(first.items.some((item: { id: string }) => item.id === target.id)).toBe(false);
  expect(first.items.some((item: { id: string }) => item.id === other.id)).toBe(false);

  let requests = 0;
  await page.route("**/api/creator-directory", async route => {
    requests++;
    if (requests === 1) {
      return route.fulfill({ status: 503, json: { detail: "受控的账号目录读取故障" } });
    }
    return route.continue();
  });
  await page.goto(`/workspace?creator=${target.id}&series=${targetSeries.id}`);
  await expect(page.getByRole("alert")).toContainText("受控的账号目录读取故障");
  await page.getByRole("button", { name: "重新连接", exact: true }).click();
  const rail = await openRail(page);
  await expect(rail.getByRole("button", { name: "切换账号", exact: true })).toContainText(target.display_name, { timeout: 15_000 });
  await expect(page.getByRole("heading", { name: targetSeries.name, exact: true })).toBeVisible();
  await expect(page.getByText("账号不存在，请从左侧重新选择。", { exact: true })).toHaveCount(0);
  const launcher = page.getByRole("button", { name: "打开账号对话" });
  await expect(launcher).toHaveAttribute("title", `与 ${target.display_name} 对话`);

  const switcher = await openSwitcher(page);
  await switcher.getByLabel("搜索账号", { exact: true }).fill(other.display_name);
  await expect(switcher.getByRole("button", { name: `切换到账号 ${other.display_name}`, exact: true })).toBeVisible();
  await switcher.getByRole("button", { name: `切换到账号 ${other.display_name}`, exact: true }).click();
  await expectLocation(page, other.id);
  await expect(rail.getByRole("button", { name: otherSeries.name, exact: true })).toBeVisible();
  await expect(rail.getByRole("button", { name: targetSeries.name, exact: true })).toHaveCount(0);
  await page.reload();
  await expect(rail.getByRole("button", { name: "切换账号", exact: true })).toContainText(other.display_name, { timeout: 15_000 });
  await expect(page.getByRole("heading", { name: other.display_name, exact: true })).toBeVisible();
  const createFromSwitcher = await openSwitcher(page);
  await createFromSwitcher.getByRole("button", { name: "新建账号", exact: true }).click();
  const createDialog = page.getByRole("dialog", { name: "新建账号", exact: true });
  const newName = `超过百个后新建-${suffix}`;
  await createDialog.getByLabel("新账号名称", { exact: true }).fill(newName);
  await createDialog.getByRole("button", { name: "创建", exact: true }).click();
  await expect(page.getByRole("heading", { name: newName, exact: true })).toBeVisible({ timeout: 20_000 });
  await page.goto(`/workspace?creator=${target.id}&series=${otherSeries.id}`);
  await expect(page.getByText("栏目不存在或不属于当前账号，请从左侧重新选择。", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: otherSeries.name, exact: true })).toHaveCount(0);

  // A saved Skill composer draft must not silently submit as unassigned if
  // the single directory read fails.
  const seeded = await post(request, "/test/skill-files", {});
  const seriesName = `跨页账号创建栏目-${suffix}`;
  await page.evaluate(({ skillIds, accountId, name }) => sessionStorage.setItem("creatoros:skill-composer:v1",
    JSON.stringify({ ids: skillIds, name, creatorId: accountId, instruction: "", mode: "reference" })),
  { skillIds: [seeded.mind.id, seeded.production.id], accountId: other.id, name: seriesName });
  await page.unroute("**/api/creator-directory");
  let skillPageFailed = false;
  await page.route("**/api/creator-directory", route => {
    if (!skillPageFailed) {
      skillPageFailed = true;
      return route.fulfill({ status: 503, json: { detail: "Skill 库账号目录读取故障" } });
    }
    return route.continue();
  });
  await page.goto("/skills");
  await expect(page.getByRole("heading", { name: "Skill 库", exact: true })).toBeVisible();
  await expect(page.getByText("账号列表读取失败", { exact: true })).toBeVisible();
  const createColumn = page.getByRole("button", { name: "创建栏目", exact: true });
  await expect(createColumn).toBeDisabled();
  await page.getByRole("button", { name: "重试加载账号", exact: true }).click();
  const owner = page.getByRole("combobox", { name: "归属账号", exact: true });
  await expect(owner).toHaveValue(other.id);
  await expect(owner.getByRole("option", { name: other.display_name, exact: true })).toBeAttached();
  await expect(createColumn).toBeEnabled();
  const created = page.waitForResponse(response => response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/series");
  await createColumn.click();
  const result = await created;
  expect(result.ok(), await result.text()).toBeTruthy();
  const saved = await result.json();
  expect(saved.series).toMatchObject({ name: seriesName, creator_id: other.id });
  expect((await request.get(`/api/series/${saved.series.id}`).then(response => response.json())).creator_id).toBe(other.id);
});

test("account directory refreshes after concurrent changes without mixing pages", async ({ page, request }) => {
  const suffix = crypto.randomUUID().slice(0, 8);
  const removed = await post(request, "/api/creators", { display_name: `目录旧账号-${suffix}` });
  const selected = await post(request, "/api/creators", { display_name: `目录当前账号-${suffix}` });
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let started = false, snapshot: { id: string }[] = [];
  await page.route("**/api/creator-directory", async route => {
    if (started) return route.continue();
    const response = await route.fetch();
    snapshot = (await response.json()).items;
    started = true;
    await gate;
    try { await route.fulfill({ response }); } catch { /* navigation may cancel the request */ }
  });
  try {
    await page.goto(`/workspace?creator=${selected.id}`);
    await expect.poll(() => started).toBe(true);
    const deletion = await request.post(`/api/creators/${removed.id}/delete`, { data: {} });
    expect(deletion.ok(), await deletion.text()).toBeTruthy();
    const added = await post(request, "/api/creators", { display_name: `目录新账号-${suffix}` });
    expect(snapshot.some(item => item.id === removed.id)).toBe(true);
    expect(snapshot.some(item => item.id === added.id)).toBe(false);
    release();
    await expect(page.getByRole("heading", { name: selected.display_name, exact: true })).toBeVisible();
    // An already returned snapshot is not live; a fresh load must reflect both changes.
    await page.reload();
    const switcher = await openSwitcher(page);
    await switcher.getByLabel("搜索账号", { exact: true }).fill(added.display_name);
    await expect(switcher.getByRole("button", { name: `切换到账号 ${added.display_name}`, exact: true })).toBeVisible();
    await switcher.getByLabel("搜索账号", { exact: true }).fill(removed.display_name);
    await expect(switcher.getByRole("button", { name: `切换到账号 ${removed.display_name}`, exact: true })).toHaveCount(0);
    await expect(switcher.getByText("没有匹配的账号", { exact: true })).toBeVisible();
  } finally { release(); }
});

test("target account distinguishes a missing account from a failed detail read", async ({ page, request }) => {
  const account = await post(request, "/api/creators", { display_name: `账号读取故障-${crypto.randomUUID().slice(0, 8)}` });
  let failed = false;
  await page.route(`**/api/creators/${account.id}`, async route => {
    if (!failed) {
      failed = true;
      return route.fulfill({ status: 503, json: { detail: "受控的账号读取故障" } });
    }
    return route.continue();
  });
  await page.goto(`/workspace?creator=${account.id}`);
  await expect(page.getByRole("alert")).toContainText("读取当前账号失败");
  await expect(page.getByText("账号不存在，请从左侧重新选择。", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "打开账号对话" })).toBeDisabled();
  await page.getByRole("button", { name: "重新连接", exact: true }).click();
  await expect(page.getByRole("heading", { name: account.display_name, exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "打开账号对话" })).toHaveAttribute("title", `与 ${account.display_name} 对话`);
  await page.goto("/workspace?creator=missing-account-fixture");
  await expect(page.getByText("账号不存在，请从左侧重新选择。", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "打开账号对话" })).toBeDisabled();
});

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
