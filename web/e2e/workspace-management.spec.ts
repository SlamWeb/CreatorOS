import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

async function create(request: APIRequestContext, path: string, data: object) {
  const response = await request.post(path, { data });
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}

async function openSettings(page: Page) {
  await page.getByRole("button", { name: "栏目设置", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "栏目设置", exact: true });
  await expect(drawer).toBeVisible();
  return drawer;
}

async function workspaceFixture(request: APIRequestContext, topicCount = 0, includePair = false) {
  const suffix = crypto.randomUUID().slice(0, 8);
  const account = await create(request, "/api/creators", { display_name: `工作区管理-${suffix}` });
  const empty = await create(request, `/api/creators/${account.id}/series`, { name: `空栏目保留在管理树-${suffix}` });
  const skills = includePair ? await create(request, "/test/skill-files", {}) : null;
  const seriesResult = includePair
    ? await create(request, "/api/series", {
        name: `真实绑定 Skill 的长栏目-${suffix}`, description: "隔离验收", audience: "测试用户",
        creator_id: account.id, mind_skill_id: skills.mind.id,
        production_skill_id: skills.production.id, request_id: crypto.randomUUID().replaceAll("-", ""),
      })
    : await create(request, `/api/creators/${account.id}/series`, { name: `真实绑定 Skill 的长栏目-${suffix}` });
  const series = seriesResult.series ?? seriesResult;
  const topicIds: string[] = [];
  if (topicCount) {
    const queued = await create(request, `/api/series/${series.id}/queue`, {
      request_id: crypto.randomUUID(),
      topics: Array.from({ length: topicCount }, (_, index) => ({ title: `长栏目滚动验收选题 ${index + 1}` })),
    });
    topicIds.push(...queued.topic_ids);
  }
  return { account, empty, series, skills, topicIds };
}

test("workspace tree keeps empty columns manageable, Skill links inspect real files, and main navigation stays fixed", async ({ page, request }, info) => {
  const data = await workspaceFixture(request, 24, true);
  await page.goto(`/?creator=${data.account.id}`);
  await expect(page.locator(".account-nav-series .rail-series")).toHaveCount(2);
  await expect(page.getByRole("heading", { name: data.empty.name })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "当前账号的栏目" }).getByRole("button", { name: data.empty.name })).toBeVisible();
  await expect(page.locator(".rail-series").filter({ hasText: data.series.name })).toContainText(data.series.name);
  await expect(page.locator(".rail-series small, .rail-series .status-pill")).toHaveCount(0);
  expect(await page.locator(".rail-series").filter({ hasText: new RegExp(data.account.display_name.replace("工作区管理-", "")) }).allTextContents())
    .toEqual([data.empty.name, data.series.name]);
  await page.screenshot({ path: info.outputPath("workspace-management-account-desktop.png"), fullPage: true });

  await page.locator(".rail-series").filter({ hasText: data.series.name }).click();
  const mindLink = page.getByRole("button", { name: new RegExp(`^${data.skills.mind.name}(?:v1)?$`) });
  const visualLink = page.getByRole("button", { name: new RegExp(`^${data.skills.production.name}(?:v1)?$`) });
  await expect(mindLink).toBeVisible({ timeout: 30_000 });
  await expect(visualLink).toBeVisible();
  const nav = page.locator(".sidebar");
  const navTop = await nav.evaluate(element => element.getBoundingClientRect().top);
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(0);
  await expect.poll(async () => Math.abs(await nav.evaluate(element => element.getBoundingClientRect().top) - navTop)).toBeLessThan(1);
  await expect(page.getByRole("heading", { name: "长栏目滚动验收选题 20" })).toBeVisible();
  await mindLink.click();
  const inspector = page.locator("dialog.skill-inspector");
  await expect(inspector.getByRole("heading", { name: data.skills.mind.name })).toBeVisible();
  await expect(inspector.getByText("完整 Skill 结尾标记")).toBeVisible();
  await page.screenshot({ path: info.outputPath("workspace-management-skill-inspector.png"), fullPage: true });
  await inspector.getByRole("button", { name: "关闭 Skill 详情" }).click();
  await expect(mindLink).toBeFocused();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("workspace-management-mobile.png"), fullPage: true });
  await page.setViewportSize({ width: 320, height: 700 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("empty columns can be deleted permanently; columns with history are removed and restorable", async ({ page, request }, info) => {
  const data = await workspaceFixture(request, 1);
  await page.goto(`/?creator=${data.account.id}&series=${data.empty.id}`);
  const emptySettings = await openSettings(page);
  await emptySettings.getByRole("button", { name: "删除或移出栏目" }).click();
  await expect(emptySettings.getByText("永久删除后无法恢复。", { exact: false })).toBeVisible();
  const emptyDelete = page.waitForResponse(response => response.request().method() === "DELETE" && response.url().endsWith(`/api/series/${data.empty.id}`));
  await emptySettings.getByRole("button", { name: "永久删除栏目", exact: true }).click();
  const emptyResponse = await emptyDelete;
  expect(emptyResponse.ok()).toBeTruthy();
  await expect(page.getByRole("navigation", { name: "当前账号的栏目" }).getByRole("button", { name: data.empty.name })).toHaveCount(0);
  expect((await request.get(`/api/series/${data.empty.id}`)).status()).toBe(404);

  await page.goto(`/?creator=${data.account.id}&series=${data.series.id}`);
  const settings = await openSettings(page);
  await settings.getByRole("button", { name: "删除或移出栏目" }).click();
  await expect(settings.getByText("保留历史数据", { exact: false })).toBeVisible();
  await settings.getByRole("button", { name: "取消", exact: true }).click();
  await expect(settings.getByRole("button", { name: "删除或移出栏目" })).toBeFocused();
  await settings.getByRole("button", { name: "关闭栏目设置", exact: true }).click();
  await expect(page.getByRole("heading", { name: "长栏目滚动验收选题 1" })).toBeVisible();
  expect((await request.get(`/api/series/${data.series.id}/topics`).then(response => response.json())).items)
    .toEqual(expect.arrayContaining([expect.objectContaining({ id: data.topicIds[0] })]));

  await openSettings(page);
  const archived = page.waitForResponse(response => response.request().method() === "DELETE" && response.url().endsWith(`/api/series/${data.series.id}`));
  await settings.getByRole("button", { name: "删除或移出栏目" }).click();
  await settings.getByRole("button", { name: "移出工作区", exact: true }).click();
  const archivedResponse = await archived;
  const result = await archivedResponse.json();
  expect(result.status).toBe("archived");
  await expect(page.getByRole("navigation", { name: "当前账号的栏目" }).getByRole("button", { name: data.series.name })).toHaveCount(0);
  await expect.poll(async () => (await request.get(`/api/series/${data.series.id}/topics`).then(response => response.json())).items
    .some((topic: { id: string }) => topic.id === data.topicIds[0])).toBeTruthy();
  await page.getByRole("button", { name: "已移出的账号与栏目", exact: true }).click();
  const recovery = page.getByRole("dialog", { name: "已移出的账号与栏目" });
  await expect(recovery.getByRole("button", { name: `恢复 ${data.series.name}` })).toBeVisible();
  await page.screenshot({ path: info.outputPath("workspace-recovery-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: info.outputPath("workspace-recovery-mobile.png") });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await recovery.getByRole("button", { name: `恢复 ${data.series.name}` }).click();
  await expect(recovery.getByRole("status")).toContainText("已恢复");
  await recovery.getByRole("button", { name: "关闭已移出的账号与栏目" }).click();
  await page.reload();
  await expect(page.getByRole("button", { name: data.series.name, exact: true })).toBeVisible();
});

test("delete conflicts remain visible and do not trigger cancel or other writes", async ({ page, request }) => {
  const data = await workspaceFixture(request, 1);
  await page.goto(`/?creator=${data.account.id}&series=${data.series.id}`);
  const settings = await openSettings(page);
  const writes: string[] = [];
  page.on("request", event => { if (event.method() !== "GET") writes.push(new URL(event.url()).pathname); });
  await page.route(`**/api/series/${data.series.id}`, route => route.request().method() === "DELETE"
    ? route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ error: { message: "栏目仍有后台任务，稍后重试。" } }) })
    : route.continue());
  await settings.getByRole("button", { name: "删除或移出栏目" }).click();
  await settings.getByRole("button", { name: "移出工作区", exact: true }).click();
  await expect(settings.getByRole("alert")).toContainText("栏目仍有后台任务");
  expect(writes).toEqual([`/api/series/${data.series.id}`]);
  await settings.getByRole("button", { name: "关闭栏目设置", exact: true }).click();
  await expect(page.getByRole("heading", { name: "长栏目滚动验收选题 1" })).toBeVisible();
  await page.locator(".rail-series").filter({ hasText: data.empty.name }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("a delayed deletion cannot clear the newly selected series", async ({ page, request }) => {
  const data = await workspaceFixture(request);
  await page.goto(`/?creator=${data.account.id}&series=${data.series.id}`);
  await page.locator(".rail-series").filter({ hasText: data.empty.name }).click();
  const settings = await openSettings(page);
  let release!: () => void;
  const barrier = new Promise<void>(resolve => { release = resolve; });
  await page.route(`**/api/series/${data.empty.id}`, async route => {
    if (route.request().method() === "DELETE") await barrier;
    await route.continue();
  });
  const sent = page.waitForRequest(event => event.method() === "DELETE" && event.url().endsWith(`/api/series/${data.empty.id}`));
  const completed = page.waitForResponse(response => response.request().method() === "DELETE"
    && response.url().endsWith(`/api/series/${data.empty.id}`));
  await settings.getByRole("button", { name: "删除或移出栏目" }).click();
  await settings.getByRole("button", { name: "永久删除栏目", exact: true }).click();
  await sent;
  // The pending modal intentionally disables closing. Browser Back leaves the
  // column without cancelling its write, returning to the previous one.
  await page.goBack();
  await expect(page).toHaveURL(url => url.searchParams.get("series") === data.series.id);
  release();
  expect((await completed).status()).toBe(200);
  await expect(page).toHaveURL(new RegExp(`series=${data.series.id}`));
  await expect(page.getByRole("heading", { name: data.series.name, exact: true })).toBeVisible();
  await expect(page.locator(".rail-series").filter({ hasText: data.empty.name })).toHaveCount(0);
});

test("working task prevents removal and remains running after an isolated API conflict", async ({ page, request }) => {
  const data = await workspaceFixture(request, 1);
  await create(request, `/api/creators/${data.account.id}/skills`, {
    skill_id: "knowledge-to-carousel", request_id: crypto.randomUUID(),
  });
  const pool = await request.get(`/api/series/${data.series.id}/skills`).then(response => response.json());
  const configured = await request.put(`/api/series/${data.series.id}/skills`, { data: {
    expected_version: pool.version, skill_ids: ["knowledge-to-carousel"],
    defaults: { image: [{ skill_id: "knowledge-to-carousel", role: "primary" }], video: [] },
  } });
  expect(configured.ok(), await configured.text()).toBeTruthy();
  const run = await create(request, "/api/runs", { topic_id: data.topicIds[0] });
  const started = await request.post(`/api/runs/${run.id}/execute`, { data: { expected_version: run.version } });
  expect(started.status()).toBe(202);
  // 202 means accepted by the durable queue, not already claimed by its worker.
  await expect.poll(async () => (await request.get(`/api/runs/${run.id}`).then(r => r.json())).status,
    { intervals: [50, 100, 200], timeout: 10_000 }).toBe("producing");

  // Capture the real assessment while the controlled producer is active.
  const preview = await request.get(`/api/series/${data.series.id}/removal`);
  expect(await preview.json()).toMatchObject({ status: "blocked", reasons: expect.arrayContaining([expect.objectContaining({ code: "active_runs" })]) });

  const rejected = await request.delete(`/api/series/${data.series.id}`, {
    data: { expected_revision: data.series.revision, request_id: crypto.randomUUID().replaceAll("-", "") },
  });
  expect(rejected.status()).toBe(409);
  const currentRun = await request.get(`/api/runs/${run.id}`).then(response => response.json());
  expect(currentRun.status).not.toBe("cancelled");
  expect((await request.get(`/api/series/${data.series.id}`).then(response => response.json())).is_active).toBe(true);
  // Replay the captured assessment in the UI so the short producer cannot race the browser.
  await page.route(`**/api/series/${data.series.id}/removal`, route => route.fulfill({ response: preview }));
  await page.goto(`/?creator=${data.account.id}&series=${data.series.id}`);
  const settings = await openSettings(page);
  await settings.getByRole("button", { name: "删除或移出栏目" }).click();
  await expect(settings.getByText("正在制作的任务（1）")).toBeVisible();
  await expect(settings.getByRole("button", { name: "移出工作区" })).toHaveCount(0);
});

test("account removal retains history links and restores the original columns", async ({ page, request }) => {
  const data = await workspaceFixture(request, 1);
  await create(request, `/api/creators/${data.account.id}/skills`, { skill_id: "knowledge-to-carousel", request_id: crypto.randomUUID() });
  const pool = await request.get(`/api/series/${data.series.id}/skills`).then(response => response.json());
  const configured = await request.put(`/api/series/${data.series.id}/skills`, { data: {
    expected_version: pool.version, skill_ids: ["knowledge-to-carousel"],
    defaults: { image: [{ skill_id: "knowledge-to-carousel", role: "primary" }], video: [] },
  } });
  expect(configured.ok(), await configured.text()).toBeTruthy();
  const run = await create(request, "/api/runs", { topic_id: data.topicIds[0] });
  await create(request, `/api/runs/${run.id}/execute`, { expected_version: run.version });
  await expect.poll(async () => (await request.get(`/api/runs/${run.id}`).then(response => response.json())).status).toBe("awaiting_approval");
  await page.goto(`/workspace?creator=${data.account.id}&series=${data.series.id}`);
  await page.getByRole("button", { name: "切换账号", exact: true }).click();
  await page.getByRole("button", { name: "删除或移出当前账号", exact: true }).click();
  const preview = page.getByRole("dialog", { name: "删除或移出账号" });
  await expect(preview.getByText("保留历史数据", { exact: false })).toBeVisible();
  await preview.getByRole("button", { name: "移出工作区", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: `已从工作区移出「${data.account.display_name}」` })).toBeVisible();
  expect(await request.get(`/api/creators/${data.account.id}`).then(response => response.json())).toMatchObject({ is_active: false });
  await page.goto(`/workspace?creator=${data.account.id}&series=${data.series.id}`);
  await page.getByRole("button", { name: "已移出的账号与栏目", exact: true }).click();
  const recovery = page.getByRole("dialog", { name: "已移出的账号与栏目" });
  await recovery.getByRole("button", { name: `查看历史 ${data.account.display_name}` }).click();
  const history = recovery.getByRole("link", { name: "长栏目滚动验收选题 1", exact: true });
  await expect(history).toHaveAttribute("href", `/runs/${run.id}`);
  await history.click();
  await expect(page).toHaveURL(`/runs/${run.id}`);
  await expect(page.getByRole("status").filter({ hasText: "此记录可只读查看" })).toBeVisible();
  await expect(page.getByRole("region", { name: "产物图片" }).getByRole("button", { name: "放大第 1 张图片" })).toBeVisible();
  await expect(page.getByRole("button", { name: "更多", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "讨论", exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "改进 Skill", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "旧讨论", exact: true }).click();
  await expect(page.getByText("没有旧讨论记录", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "发布记录", exact: true }).click();
  await expect(page.getByText("没有发布记录。", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "准备发布单", exact: true })).toHaveCount(0);
  await page.getByRole("link", { name: "返回恢复管理", exact: true }).click();
  await recovery.getByRole("button", { name: `恢复 ${data.account.display_name}` }).click();
  await expect(recovery.getByRole("status")).toContainText("已恢复");
  await recovery.getByRole("button", { name: "关闭已移出的账号与栏目" }).click();
  await expect(page.getByRole("button", { name: data.series.name, exact: true })).toBeVisible();
});
