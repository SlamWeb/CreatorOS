import { expect, test, type APIRequestContext } from "@playwright/test";

async function create(request: APIRequestContext, path: string, data: object) {
  const response = await request.post(path, { data });
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}

async function seriesFixture(request: APIRequestContext) {
  const creator = await create(request, "/api/creators", { display_name: "移除选题隔离验收" });
  const series = await create(request, `/api/creators/${creator.id}/series`, { name: "移除选题测试栏目" });
  return { creator, series };
}

test("unproduced card removal confirms once, persists after refresh and keeps focus", async ({ page, request }, info) => {
  const { creator, series } = await seriesFixture(request);
  const queued = await create(request, `/api/series/${series.id}/queue`, {
    request_id: crypto.randomUUID(), topics: [{ title: "尚未生产的待删除内容" }],
  });
  const id = queued.topic_ids[0] as string;
  const posts: { url: string; body: { request_id: string; batch_id?: string; candidate_id?: string } }[] = [];
  page.on("request", event => {
    if (event.method() === "POST" && new URL(event.url()).pathname.endsWith("/remove")) {
      posts.push({ url: new URL(event.url()).pathname, body: event.postDataJSON() });
    }
  });
  await page.goto(`/?creator=${creator.id}&series=${series.id}&topics=queued`);
  const card = page.getByTestId(`library-${id}`);
  await expect(card).toBeVisible();
  await card.locator("summary").click();
  await card.getByRole("button", { name: "移除", exact: true }).click();
  const detail = page.getByRole("dialog", { name: "选题详情" });
  await expect(detail.getByRole("group", { name: "确认移除选题" })).toBeVisible();
  expect(posts).toHaveLength(0);
  await page.screenshot({ path: info.outputPath("remove-confirm-desktop-1440.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("remove-confirm-mobile-390.png"), fullPage: true });
  await detail.getByRole("button", { name: "确认移除" }).click();
  await expect(card).toHaveCount(0);
  await expect(detail).toHaveCount(0);
  await expect(page.locator(`#topic-library-${series.id}-main`)).toBeFocused();
  expect(posts).toHaveLength(1);
  expect(posts[0].url).toBe(`/api/topics/${id}/remove`);
  expect(posts[0].body.request_id).toMatch(/^[a-f0-9]{32}$/);
  expect(posts[0].body.batch_id).toBeUndefined();
  expect(posts[0].body.candidate_id).toBeUndefined();
  expect((await request.get(`/api/series/${series.id}/topics`).then(r => r.json())).page.total).toBe(0);
  await page.reload();
  await expect(card).toHaveCount(0);
});

test("pending suggestion is dismissed with batch identity and stays hidden after refresh", async ({ page, request }) => {
  const { creator, series } = await seriesFixture(request);
  const response = await request.post(`/test/series/${series.id}/research`, { data: {} });
  expect(response.ok(), await response.text()).toBeTruthy();
  const batch = await response.json();
  const candidate = batch.candidates[0];
  const id = `research-${batch.id}-${candidate.id}`;
  let body: { request_id: string; batch_id: string; candidate_id: string } | null = null;
  page.on("request", event => {
    if (event.method() === "POST" && new URL(event.url()).pathname === `/api/topics/${id}/remove`) {
      body = event.postDataJSON();
    }
  });
  await page.goto(`/?creator=${creator.id}&series=${series.id}&topics=pending`);
  const card = page.getByTestId(`library-${id}`);
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: /查看选题/ }).click();
  const detail = page.getByRole("dialog", { name: "选题详情" });
  await detail.getByRole("button", { name: "移除", exact: true }).click();
  await expect(detail.getByText(/从候选列表隐藏/)).toBeVisible();
  await detail.getByRole("button", { name: "确认移除" }).click();
  await expect(card).toHaveCount(0);
  expect(body).toMatchObject({ batch_id: batch.id, candidate_id: candidate.id });
  expect(body!.request_id).toMatch(/^[a-f0-9]{32}$/);
  await page.reload();
  await expect(card).toHaveCount(0);
  expect((await request.get(`/api/series/${series.id}/topics`).then(r => r.json())).page.total).toBe(0);

  const second = batch.candidates[1];
  const queued = await create(request, `/api/topic-research/${batch.id}/queue`, {
    request_id: crypto.randomUUID(), selections: [{ candidate_id: second.id }],
  });
  expect(queued.operation_id).toBeTruthy();
  const formalId = `research-${batch.id}-${second.id}`;
  await page.getByRole("button", { name: "已入队", exact: true }).click();
  await expect(page.getByTestId(`library-${formalId}`)).toBeVisible();
  await page.getByTestId(`library-${formalId}`).getByRole("button", { name: /查看选题/ }).click();
  const formalDetail = page.getByRole("dialog", { name: "选题详情" });
  await formalDetail.getByRole("button", { name: "移除", exact: true }).click();
  await formalDetail.getByRole("button", { name: "确认移除" }).click();
  await expect(page.getByTestId(`library-${formalId}`)).toHaveCount(0);
  await page.getByRole("button", { name: "全部", exact: true }).click();
  await page.reload();
  await expect(page.getByTestId(`library-${formalId}`)).toHaveCount(0);
  await expect(page.getByTestId(`library-${id}`)).toHaveCount(0);
});

test("produced card archives while its Run and verified image remain readable", async ({ page, request }, info) => {
  const { creator, series } = await seriesFixture(request);
  const queued = await create(request, `/api/series/${series.id}/queue`, {
    request_id: crypto.randomUUID(), topics: [{ title: "已生产且待归档的内容" }],
  });
  const id = queued.topic_ids[0] as string;
  const run = await create(request, "/api/runs", { topic_id: id });
  await create(request, `/api/runs/${run.id}/execute`, { expected_version: run.version });
  await expect.poll(async () => (await request.get(`/api/runs/${run.id}`).then(r => r.json())).status).toBe("awaiting_approval");
  const library = await request.get(`/api/series/${series.id}/topic-library?state=queued&offset=0&limit=20`).then(r => r.json());
  const coverUrl = library.items.find((item: { id: string }) => item.id === id).cover_url as string;
  expect((await request.get(coverUrl)).ok()).toBe(true);
  await page.goto(`/?creator=${creator.id}&series=${series.id}&topics=queued`);
  const productionTask = page.getByRole("region", { name: "任务" })
    .getByRole("link", { name: /已生产且待归档的内容/ });
  await expect(productionTask).toBeVisible();
  await expect(productionTask).toHaveAttribute("href", new RegExp(`/runs/${run.id}`));
  await expect(productionTask).toContainText("生产");
  const card = page.getByTestId(`library-${id}`);
  await expect(card.getByRole("img")).toBeVisible();
  await card.locator("summary").click();
  await card.getByRole("button", { name: "移除", exact: true }).click();
  const detail = page.getByRole("dialog", { name: "选题详情" });
  await expect(detail.getByText(/产物、生产记录和 Trace 会保留/)).toBeVisible();
  await page.screenshot({ path: info.outputPath("archive-confirm-desktop-1440.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("archive-confirm-mobile-390.png"), fullPage: true });
  await detail.getByRole("button", { name: "确认移除" }).click();
  await expect(card).toHaveCount(0);
  await expect(productionTask).toHaveCount(0);
  await page.reload();
  await expect(card).toHaveCount(0);
  await expect(productionTask).toHaveCount(0);
  const storedRun = await request.get(`/api/runs/${run.id}`);
  expect(storedRun.ok()).toBe(true);
  expect((await storedRun.json()).status).toBe("awaiting_approval");
  expect((await request.get(coverUrl)).ok()).toBe(true);
});

test("controlled 409 and lost response keep cards visible; explicit retry reuses request ID", async ({ page, request }, info) => {
  const { creator, series } = await seriesFixture(request);
  const queued = await create(request, `/api/series/${series.id}/queue`, {
    request_id: crypto.randomUUID(), topics: [{ title: "409 故障卡片" }, { title: "响应丢失卡片" }],
  });
  const [conflictId, lostId] = queued.topic_ids as string[];
  await page.goto(`/?creator=${creator.id}&series=${series.id}&topics=queued`);
  const conflictCard = page.getByTestId(`library-${conflictId}`);
  const conflictBodies: string[] = [];
  page.on("request", event => {
    if (event.method() === "POST" && new URL(event.url()).pathname === `/api/topics/${conflictId}/remove`) {
      conflictBodies.push(event.postDataJSON().request_id);
    }
  });
  await page.route(`**/api/topics/${conflictId}/remove`, route => {
    return route.fulfill({ status: 409, json: { error: { code: "active_run", message: "生产仍在进行，请先停止或等待完成后再移除。" } } });
  });
  await conflictCard.getByRole("button", { name: /查看选题/ }).click();
  let detail = page.getByRole("dialog", { name: "选题详情" });
  await detail.getByRole("button", { name: "移除", exact: true }).click();
  await detail.getByRole("button", { name: "确认移除" }).click();
  await expect(detail.getByRole("alert")).toContainText("先停止");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("remove-409-mobile-390.png"), fullPage: true });
  await expect(conflictCard).toBeVisible();
  await page.unroute(`**/api/topics/${conflictId}/remove`);
  await detail.getByRole("button", { name: "重试移除" }).click();
  await expect(conflictCard).toHaveCount(0);
  expect(conflictBodies).toHaveLength(2);
  expect(conflictBodies[1]).toBe(conflictBodies[0]);

  const lostCard = page.getByTestId(`library-${lostId}`);
  const lostBodies: string[] = [];
  await page.route(`**/api/topics/${lostId}/remove`, async route => {
    lostBodies.push(route.request().postDataJSON().request_id);
    await route.fetch(); // The isolated server commits, then the browser loses its response.
    await route.abort("failed");
  });
  await lostCard.getByRole("button", { name: /查看选题/ }).click();
  detail = page.getByRole("dialog", { name: "选题详情" });
  await detail.getByRole("button", { name: "移除", exact: true }).click();
  await detail.getByRole("button", { name: "确认移除" }).click();
  await expect(detail.getByRole("button", { name: "重试移除" })).toBeVisible();
  await expect(lostCard).toBeVisible();
  await page.unroute(`**/api/topics/${lostId}/remove`);
  page.on("request", event => {
    if (event.method() === "POST" && new URL(event.url()).pathname === `/api/topics/${lostId}/remove`) {
      lostBodies.push(event.postDataJSON().request_id);
    }
  });
  await detail.getByRole("button", { name: "重试移除" }).click();
  await expect(lostCard).toHaveCount(0);
  expect(lostBodies).toHaveLength(2);
  expect(lostBodies[1]).toBe(lostBodies[0]);
});

test("late success for a closed detail does not close the next card", async ({ page, request }) => {
  const { creator, series } = await seriesFixture(request);
  const queued = await create(request, `/api/series/${series.id}/queue`, {
    request_id: crypto.randomUUID(), topics: [{ title: "旧请求卡片" }, { title: "新打开的卡片" }],
  });
  const [oldId, nextId] = queued.topic_ids as string[];
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let committed!: () => void;
  const serverCommitted = new Promise<void>(resolve => { committed = resolve; });
  await page.route(`**/api/topics/${oldId}/remove`, async route => {
    const response = await route.fetch();
    committed();
    await gate;
    await route.fulfill({ response });
  });
  await page.goto(`/?creator=${creator.id}&series=${series.id}&topics=queued`);
  await page.getByTestId(`library-${oldId}`).getByRole("button", { name: /查看选题/ }).click();
  await page.getByRole("dialog", { name: "选题详情" }).getByRole("button", { name: "移除", exact: true }).click();
  await page.getByRole("button", { name: "确认移除" }).click();
  await serverCommitted;
  await page.getByRole("button", { name: "关闭选题详情" }).click();
  await page.getByTestId(`library-${nextId}`).getByRole("button", { name: /查看选题/ }).click();
  release();
  await expect(page.getByRole("dialog", { name: "选题详情" }).getByRole("heading", { name: "新打开的卡片" })).toBeVisible();
  await expect.poll(() => new URL(page.url()).searchParams.get("topic")).toBe(nextId);
});
