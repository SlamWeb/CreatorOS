import { expect, test, type APIRequestContext } from "@playwright/test";
import { configureImageDefault } from "./fixtures/image-default";

async function post(request: APIRequestContext, path: string, data: object) {
  const response = await request.post(path, { data });
  expect(response.ok(), await response.text()).toBe(true);
  return response.json();
}

test("a Run Skill waits for the catalog and opens after the first click", async ({ page, request }) => {
  const account = await post(request, "/api/creators", { display_name: "Skill 读取时序" });
  const series = await post(request, `/api/creators/${account.id}/series`, { name: "查看作品 Skill" });
  await configureImageDefault(request, account.id, series.id);
  const queue = await post(request, `/api/series/${series.id}/queue`, {
    request_id: crypto.randomUUID(), topics: [{ title: "加载中的 Skill" }],
  });
  const run = await post(request, "/api/runs", { topic_id: queue.topic_ids[0] });
  const catalog = await request.get("/api/producer-skills").then(response => response.json());
  const builtin = catalog.items.find((item: { id: string }) => item.id === "knowledge-to-carousel");
  expect(builtin).toBeTruthy();
  let releaseCatalog!: () => void;
  const catalogGate = new Promise<void>(resolve => { releaseCatalog = resolve; });
  let catalogWaiting = false;
  let versionRequests = 0;
  const builtinVersions = /\/api\/producer-skills\/knowledge-to-carousel\/versions(?:\?.*)?$/;
  await page.route(/\/api\/producer-skills$/, async route => {
    const response = await route.fetch();
    catalogWaiting = true;
    await catalogGate;
    await route.fulfill({ response });
  });
  await page.route(builtinVersions, route => {
    versionRequests++;
    return route.fulfill({ status: 422, json: { error: { message: "内置 Skill 没有版本列表" } } });
  });
  try {
    await page.goto(`/runs/${run.id}`);
    await expect.poll(() => catalogWaiting).toBe(true);
    const skill = page.getByRole("region", { name: "本次运行使用的 Skill" })
      .getByRole("button", { name: "knowledge-to-carousel", exact: true });
    await expect(skill).toBeVisible();
    await expect(skill).toBeDisabled();
    await expect(skill).toHaveAttribute("aria-busy", "true");
    expect(versionRequests).toBe(0);
    releaseCatalog();
    await expect(skill).toBeEnabled();
    await skill.click();
    await expect(page.getByRole("dialog", { name: "knowledge-to-carousel" })).toBeVisible();
    expect(versionRequests).toBe(0);
    await page.getByRole("button", { name: "关闭 Skill 详情" }).click();

    // A historic version may be absent from the current catalog. A successful
    // retry should open it immediately rather than requiring a second click.
    await page.unroute(/\/api\/producer-skills$/);
    await page.unroute(builtinVersions);
    await page.route(/\/api\/producer-skills$/, route => route.fulfill({ json: { items: [] } }));
    let attempts = 0;
    await page.route(builtinVersions, route => {
      attempts++;
      return attempts === 1
        ? route.fulfill({ status: 503, json: { error: { message: "暂时无法读取版本" } } })
        : route.fulfill({ json: { family_id: builtin.id, items: [builtin], bindings: [] } });
    });
    await page.reload();
    await expect(skill).toBeEnabled();
    expect(attempts).toBe(1);
    await skill.click();
    await expect(page.getByRole("dialog", { name: "knowledge-to-carousel" })).toBeVisible();
    expect(attempts).toBe(2);
  } finally {
    releaseCatalog();
  }
});

test("content workbench exposes actions, keeps details on demand and archives through the existing API", async ({ page, request }, info) => {
  const account = await post(request, "/api/creators", { display_name: "工作台隔离验收" });
  const series = await post(request, `/api/creators/${account.id}/series`, { name: "词汇工作台" });
  await configureImageDefault(request, account.id, series.id);
  const queued = await post(request, `/api/series/${series.id}/queue`, {
    request_id: crypto.randomUUID(), topics: [{ title: "afraid / scared / frightened / terrified：害怕的几种表达" }],
  });
  const run = await post(request, "/api/runs", { topic_id: queued.topic_ids[0] });
  await post(request, `/api/runs/${run.id}/execute`, { expected_version: run.version });
  await expect.poll(async () => (await request.get(`/api/runs/${run.id}`).then(r => r.json())).status, { timeout: 30_000 }).toBe("awaiting_approval");
  const stored = await request.get(`/api/runs/${run.id}`).then(r => r.json());
  let writes = 0;
  page.on("request", req => { if (req.url().includes("/api/") && req.method() !== "GET") writes++; });
  await page.goto(`/runs/${run.id}`);
  const approve = page.getByRole("button", { name: "批准第 1 版" });
  const revise = page.getByRole("button", { name: "提出返工" });
  const more = page.getByRole("button", { name: "更多", exact: true });
  const remove = page.getByRole("button", { name: "删除该条内容" });
  for (const button of [approve, revise, more]) {
    await expect(button).toBeVisible();
    expect((await button.boundingBox())!.y).toBeLessThan(400);
  }
  await expect(page.getByRole("link", { name: "返回栏目", exact: true })).toHaveAttribute("data-slot", "button");
  await expect(page.getByText("内容正确性请逐张验收")).toHaveCount(0);
  await expect(page.getByText("本次运行使用的 Skill", { exact: true })).toHaveCount(0);
  await expect(page.getByText("只讨论，不会修改图片、文案或审批状态")).toHaveCount(0);
  await expect(page.getByText("请先检查全部图片。批准只记录验收，不会发布。")).toHaveCount(0);
  await page.getByRole("button", { name: "查看第 2 张" }).click();
  await expect(page.locator(".carousel-paging")).toContainText("2 / 3");
  await page.getByRole("button", { name: "放大图片", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "放大图片" })).toBeVisible();
  await page.keyboard.press("Escape");
  await revise.click();
  await expect(page.getByLabel("告诉生产者要改哪里")).toBeVisible();
  await page.getByRole("button", { name: "收起", exact: true }).click();
  await page.getByRole("button", { name: "讨论", exact: true }).click();
  const conversation = page.locator(".account-chat-panel");
  await expect(conversation).toBeVisible();
  await expect(conversation.getByLabel("给 Agent 的消息")).toHaveValue(new RegExp(run.id));
  await conversation.getByLabel("给 Agent 的消息").fill("未发送的讨论草稿");
  await conversation.getByRole("button", { name: "关闭账号对话", exact: true }).click();
  await page.getByRole("button", { name: "打开账号对话", exact: true }).click();
  await expect(conversation.getByLabel("给 Agent 的消息")).toHaveValue("未发送的讨论草稿");
  await conversation.getByRole("button", { name: "关闭账号对话", exact: true }).click();
  await page.screenshot({ path: info.outputPath("workbench-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("workbench-mobile.png"), fullPage: true });
  await revise.click();
  await more.click();
  await remove.click();
  await expect(page.getByRole("group", { name: "确认删除内容" })).toBeVisible();
  expect(await page.locator(".run-review-console").evaluate(el => getComputedStyle(el).position)).toBe("static");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("workbench-mobile-expanded.png"), fullPage: true });
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("button", { name: "收起", exact: true }).click();
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole("button", { name: "技术记录", exact: true }).click();
  await page.getByRole("navigation", { name: "技术记录入口" }).getByRole("link", { name: "Observation", exact: true }).click();
  await expect(page.getByRole("heading", { name: "公开时间线" })).toBeVisible();
  await page.goto(`/runs/${run.id}`);
  await more.click();
  await remove.click();
  await expect(page.getByRole("group", { name: "确认删除内容" })).toBeVisible();
  expect(writes).toBe(0);
  await page.getByRole("button", { name: "确认删除", exact: true }).click();
  await expect(page).not.toHaveURL(/\/runs\//);
  expect(writes).toBe(1);
  expect((await request.get(`/api/series/${series.id}/topics`).then(r => r.json())).page.total).toBe(0);
  expect((await request.get(`/api/runs/${run.id}`).then(r => r.json())).status).toBe("awaiting_approval");
  expect((await request.get(stored.revisions[0].cards[0].url)).ok()).toBe(true);
});

test("an invalid revision link is read-only until the user returns to the current version", async ({ page, request }) => {
  const account = await post(request, "/api/creators", { display_name: "版本链接隔离验收" });
  const series = await post(request, `/api/creators/${account.id}/series`, { name: "无效版本" });
  await configureImageDefault(request, account.id, series.id);
  const queue = await post(request, `/api/series/${series.id}/queue`, { request_id: crypto.randomUUID(), topics: [{ title: "版本校验" }] });
  const run = await post(request, "/api/runs", { topic_id: queue.topic_ids[0] });
  let writes = 0;
  page.on("request", req => { if (req.url().includes("/api/") && req.method() !== "GET") writes++; });
  await page.goto(`/runs/${run.id}?revision=missing-revision`);
  await expect(page.getByRole("alert")).toContainText("找不到这个内容版本");
  for (const name of ["开始生产", "取消此任务", "删除该条内容"]) await expect(page.getByRole("button", { name, exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "返回当前版本", exact: true }).click();
  await expect(page.getByRole("button", { name: "开始生产", exact: true })).toBeVisible();
  expect(writes).toBe(0);
});

test("a delayed content removal cannot navigate away from the next Run", async ({ page, request }) => {
  const account = await post(request, "/api/creators", { display_name: "迟到删除隔离验收" });
  const series = await post(request, `/api/creators/${account.id}/series`, { name: "迟到响应" });
  await configureImageDefault(request, account.id, series.id);
  const queue = await post(request, `/api/series/${series.id}/queue`, { request_id: crypto.randomUUID(), topics: [{ title: "内容 A" }, { title: "内容 B" }] });
  const first = await post(request, "/api/runs", { topic_id: queue.topic_ids[0] });
  const second = await post(request, "/api/runs", { topic_id: queue.topic_ids[1] });
  let accepted = false;
  let release!: () => void;
  const responseGate = new Promise<void>(resolve => { release = resolve; });
  await page.route(`**/api/topics/${first.topic_id}/remove`, async route => {
    const response = await route.fetch();
    accepted = true;
    await responseGate;
    await route.fulfill({ response });
  });
  await page.goto(`/runs/${first.id}`);
  await page.getByRole("button", { name: "更多", exact: true }).click();
  await page.getByRole("button", { name: "删除该条内容", exact: true }).click();
  const removedResponse = page.waitForResponse(response => response.url().includes(`/api/topics/${first.topic_id}/remove`));
  await page.getByRole("button", { name: "确认删除", exact: true }).click();
  await expect.poll(() => accepted).toBe(true);
  // SPA navigation keeps the original request alive; a full page navigation would abort it.
  await page.getByRole("link", { name: "返回栏目", exact: true }).click();
  await page.getByRole("link", { name: "查看内容 内容 B", exact: true }).click();
  await expect(page.getByRole("heading", { name: "内容 B", exact: true })).toBeVisible();
  release();
  await removedResponse;
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("heading", { name: "内容 B", exact: true })).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`/runs/${second.id}`));
  await expect.poll(async () => (await request.get(`/api/series/${series.id}/topics`).then(r => r.json())).page.total).toBe(1);
});
