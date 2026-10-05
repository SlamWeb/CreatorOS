import { expect, test, type APIRequestContext } from "@playwright/test";

async function create(request: APIRequestContext, path: string, data: object) {
  const response = await request.post(path, { data });
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
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
  await expect(page.locator(".account-series-section")).toHaveCount(1);
  await expect(page.getByRole("heading", { name: data.empty.name })).toHaveCount(0);
  await expect(page.locator(".workspace-rail").getByRole("button", { name: data.empty.name })).toBeVisible();
  await expect(page.locator(".rail-series").filter({ hasText: data.series.name })).toContainText(data.series.name);
  await expect(page.locator(".rail-series small, .rail-series .status-pill")).toHaveCount(0);
  expect(await page.locator(".rail-series").filter({ hasText: new RegExp(data.account.display_name.replace("工作区管理-", "")) }).allTextContents())
    .toEqual([data.empty.name, data.series.name]);
  await page.screenshot({ path: info.outputPath("workspace-management-account-desktop.png"), fullPage: true });

  await page.locator(".rail-series").filter({ hasText: data.series.name }).click();
  const mindLink = page.getByRole("button", { name: data.skills.mind.name, exact: true });
  const visualLink = page.getByRole("button", { name: data.skills.production.name, exact: true });
  await expect(mindLink).toBeVisible();
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

test("empty columns delete immediately; nonempty columns require the stated confirmation and retain their topics", async ({ page, request }) => {
  const data = await workspaceFixture(request, 1);
  await page.goto(`/?creator=${data.account.id}&series=${data.empty.id}`);
  let confirmCalls = 0;
  const autoAccept = (dialog: import("@playwright/test").Dialog) => { confirmCalls++; void dialog.accept(); };
  page.on("dialog", autoAccept);
  const emptyDelete = page.waitForResponse(response => response.request().method() === "DELETE" && response.url().endsWith(`/api/series/${data.empty.id}`));
  await page.getByRole("button", { name: `删除栏目 ${data.empty.name}` }).click();
  const emptyResponse = await emptyDelete;
  expect(emptyResponse.ok()).toBeTruthy();
  expect(confirmCalls).toBe(0);
  page.off("dialog", autoAccept);
  await expect(page.locator(".workspace-rail").getByRole("button", { name: data.empty.name })).toHaveCount(0);

  await page.goto(`/?creator=${data.account.id}&series=${data.series.id}`);
  let confirmationText = "";
  page.once("dialog", async dialog => { confirmationText = dialog.message(); await dialog.dismiss(); });
  await page.getByRole("button", { name: `删除栏目 ${data.series.name}` }).click();
  expect(confirmationText).toBe("从工作区移除，保留选题、图片与记录");
  await expect(page.getByRole("heading", { name: "长栏目滚动验收选题 1" })).toBeVisible();
  expect((await request.get(`/api/series/${data.series.id}/topics`).then(response => response.json())).items)
    .toEqual(expect.arrayContaining([expect.objectContaining({ id: data.topicIds[0] })]));

  page.once("dialog", dialog => { void dialog.accept(); });
  const archived = page.waitForResponse(response => response.request().method() === "DELETE" && response.url().endsWith(`/api/series/${data.series.id}`));
  await page.getByRole("button", { name: `删除栏目 ${data.series.name}` }).click();
  const archivedResponse = await archived;
  const result = await archivedResponse.json();
  expect(result.status).toBe("archived");
  await expect(page.locator(".workspace-rail").getByRole("button", { name: data.series.name })).toHaveCount(0);
  await expect.poll(async () => (await request.get(`/api/series/${data.series.id}/topics`).then(response => response.json())).items
    .some((topic: { id: string }) => topic.id === data.topicIds[0])).toBeTruthy();
});

test("delete conflicts remain visible and do not trigger cancel or other writes", async ({ page, request }) => {
  const data = await workspaceFixture(request, 1);
  await page.goto(`/?creator=${data.account.id}&series=${data.series.id}`);
  const writes: string[] = [];
  page.on("request", event => { if (event.method() !== "GET") writes.push(new URL(event.url()).pathname); });
  await page.route(`**/api/series/${data.series.id}`, route => route.request().method() === "DELETE"
    ? route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ error: { message: "栏目仍有后台任务，稍后重试。" } }) })
    : route.continue());
  page.once("dialog", dialog => { void dialog.accept(); });
  await page.getByRole("button", { name: `删除栏目 ${data.series.name}` }).click();
  await expect(page.getByRole("alert")).toContainText("栏目仍有后台任务");
  expect(writes).toEqual([`/api/series/${data.series.id}`]);
  await expect(page.getByRole("heading", { name: "长栏目滚动验收选题 1" })).toBeVisible();
  await page.locator(".rail-series").filter({ hasText: data.empty.name }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("a delayed deletion cannot clear the newly selected series", async ({ page, request }) => {
  const data = await workspaceFixture(request);
  await page.goto(`/?creator=${data.account.id}&series=${data.empty.id}`);
  let release!: () => void;
  const barrier = new Promise<void>(resolve => { release = resolve; });
  await page.route(`**/api/series/${data.empty.id}`, async route => {
    if (route.request().method() === "DELETE") await barrier;
    await route.continue();
  });
  const sent = page.waitForRequest(event => event.method() === "DELETE" && event.url().endsWith(`/api/series/${data.empty.id}`));
  const completed = page.waitForResponse(response => response.request().method() === "DELETE"
    && response.url().endsWith(`/api/series/${data.empty.id}`));
  await page.getByRole("button", { name: `删除栏目 ${data.empty.name}` }).click();
  await sent;
  await page.locator(".rail-series").filter({ hasText: data.series.name }).click();
  release();
  expect((await completed).status()).toBe(200);
  await expect(page).toHaveURL(new RegExp(`series=${data.series.id}`));
  await expect(page.getByRole("heading", { name: data.series.name, exact: true })).toBeVisible();
  await expect(page.locator(".rail-series").filter({ hasText: data.empty.name })).toHaveCount(0);
});

test("isolated API rejects deleting an actively producing series without cancelling its run", async ({ request }) => {
  const data = await workspaceFixture(request, 1);
  const run = await create(request, "/api/runs", { topic_id: data.topicIds[0] });
  const started = await request.post(`/api/runs/${run.id}/execute`, { data: { expected_version: run.version } });
  expect(started.status()).toBe(202);
  expect((await started.json()).status).toBe("producing");

  const rejected = await request.delete(`/api/series/${data.series.id}`, {
    data: { expected_revision: data.series.revision, request_id: crypto.randomUUID().replaceAll("-", "") },
  });
  expect(rejected.status()).toBe(409);
  const currentRun = await request.get(`/api/runs/${run.id}`).then(response => response.json());
  expect(currentRun.status).not.toBe("cancelled");
  expect((await request.get(`/api/series/${data.series.id}`).then(response => response.json())).is_active).toBe(true);
});
