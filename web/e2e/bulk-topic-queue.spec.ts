import { expect, test, type APIRequestContext } from "@playwright/test";

async function fixture(request: APIRequestContext, count = 1) {
  const create = async (path: string, data: object = {}) => {
    const response = await request.post(path, { data });
    expect(response.ok(), await response.text()).toBe(true);
    return response.json();
  };
  const creator = await create("/api/creators", { display_name: "批量入队隔离验收" });
  const series = await create(`/api/creators/${creator.id}/series`, { name: "待选批量入队" });
  const batches = [];
  for (let index = 0; index < count; index++) batches.push(await create(`/test/series/${series.id}/research`));
  return { creator, series, batches, create };
}

test("select all visible candidates across batches, deselect, queue through SQLite and reload without production", async ({ page, request }, info) => {
  // Deterministic research candidates; browser writes and database-backed API are real.
  const data = await fixture(request, 5);
  const queuePosts: { batch: string; body: { selections: { candidate_id: string }[] } }[] = [];
  const productionPosts: string[] = [];
  page.on("request", req => {
    if (req.method() !== "POST") return;
    const path = new URL(req.url()).pathname;
    if (/\/api\/topic-research\/[^/]+\/queue$/.test(path)) queuePosts.push({ batch: path.split("/")[3], body: req.postDataJSON() });
    if (/\/api\/runs/.test(path)) productionPosts.push(path);
  });
  await page.goto(`/?creator=${data.creator.id}&series=${data.series.id}&topics=pending`);
  const toolbar = page.getByRole("group", { name: "批量入队", exact: true });
  await expect(toolbar).toContainText("10 项可入队");
  const all = toolbar.getByRole("checkbox", { name: "全选本页待选", exact: true });
  await all.check();
  await expect(page.getByRole("checkbox", { name: /选择入队/ })).toHaveCount(10);
  expect((await request.get(`/api/series/${data.series.id}/topics`).then(r => r.json())).page.total).toBe(0);
  const omitted = `research-${data.batches[0].id}-c2`;
  await page.getByTestId(`library-${omitted}`).getByRole("checkbox").uncheck();
  await expect.poll(() => all.evaluate((el: HTMLInputElement) => el.indeterminate)).toBe(true);
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: info.outputPath(`bulk-queue-${width}.png`), fullPage: true });
  }
  await toolbar.getByRole("button", { name: "确认入队（9）", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "已入队 9 项。" })).toBeVisible();
  const actual = await request.get(`/api/series/${data.series.id}/topics`).then(r => r.json());
  expect(actual.page.total).toBe(9);
  expect(actual.items.map((topic: { id: string }) => topic.id)).not.toContain(omitted);
  expect(actual.items.every((topic: { brief: string; existing_run_id: string | null }) => topic.brief.includes("参考来源") && topic.existing_run_id === null)).toBe(true);
  expect(queuePosts).toHaveLength(5);
  for (const post of queuePosts) {
    expect(data.batches.some(batch => batch.id === post.batch)).toBe(true);
    expect(post.body.selections.every(s => s.candidate_id === "c1" || s.candidate_id === "c2")).toBe(true);
  }
  await expect(toolbar).toContainText("1 项可入队");
  await expect(all).not.toBeChecked();
  await page.reload(); await expect(page.getByRole("group", { name: "批量入队", exact: true })).toContainText("1 项可入队");
  expect(queuePosts).toHaveLength(5); expect(productionPosts).toEqual([]);
  await page.getByRole("checkbox", { name: "全选本页待选", exact: true }).check();
  await page.getByRole("button", { name: "已入队", exact: true }).click();
  await expect(page.getByRole("group", { name: "批量入队", exact: true })).toHaveCount(0);
  await expect(page.getByRole("checkbox", { name: /选择入队/ })).toHaveCount(0);
  await page.getByRole("button", { name: "待选", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "全选本页待选", exact: true })).not.toBeChecked();
});

test("partially failed batches retain selections and reuse the same request on explicit retry", async ({ page, request }) => {
  const data = await fixture(request, 2);
  // A pre-queued item must never be selected a second time.
  await data.create(`/api/topic-research/${data.batches[0].id}/queue`, {
    request_id: crypto.randomUUID(), selections: [{ candidate_id: "c1" }],
  });
  const attempts: { request_id: string; selections: unknown[] }[] = [];
  let reject = true;
  await page.route(`**/api/topic-research/${data.batches[1].id}/queue`, async route => {
    attempts.push(route.request().postDataJSON());
    if (reject) await route.fulfill({ status: 503, json: { error: { message: "受控批次暂不可用" } } });
    else await route.continue();
  });
  await page.goto(`/?creator=${data.creator.id}&series=${data.series.id}`);
  await page.getByRole("checkbox", { name: "全选本页待选", exact: true }).check();
  const previouslyQueued = page.getByTestId(`library-research-${data.batches[0].id}-c1`);
  await expect(previouslyQueued.getByRole("checkbox", { name: /选择入队/ })).toHaveCount(0);
  await expect(previouslyQueued.getByRole("checkbox", { name: /选择生产/ })).not.toBeChecked();
  await page.getByRole("button", { name: "确认入队（3）", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("受控批次暂不可用");
  await expect(page.getByRole("button", { name: "确认入队（2）", exact: true })).toBeEnabled();
  expect((await request.get(`/api/series/${data.series.id}/topics`).then(r => r.json())).page.total).toBe(2);
  reject = false;
  await page.getByRole("button", { name: "确认入队（2）", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "已入队 2 项。" })).toBeVisible();
  await expect(page.getByRole("group", { name: "批量入队", exact: true })).toHaveCount(0);
  expect((await request.get(`/api/series/${data.series.id}/topics`).then(r => r.json())).page.total).toBe(4);
  expect(attempts).toHaveLength(2); expect(attempts[0]).toEqual(attempts[1]);
});

test("lost queue response refreshes persisted state without automatically resubmitting", async ({ page, request }) => {
  const data = await fixture(request);
  let posts = 0;
  await page.route(`**/api/topic-research/${data.batches[0].id}/queue`, async route => {
    posts++;
    const response = await route.fetch();
    expect(response.status()).toBe(201);
    await route.abort("failed");
  });
  await page.goto(`/?creator=${data.creator.id}&series=${data.series.id}&topics=pending`);
  await page.getByRole("checkbox", { name: "全选本页待选", exact: true }).check();
  await page.getByRole("button", { name: "确认入队（2）", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("请先刷新核对");
  await expect(page.getByText("暂无待选建议。", { exact: true })).toBeVisible();
  expect((await request.get(`/api/series/${data.series.id}/topics`).then(r => r.json())).page.total).toBe(2);
  await page.reload(); await expect(page.getByText("暂无待选建议。", { exact: true })).toBeVisible();
  expect(posts).toBe(1);
});
