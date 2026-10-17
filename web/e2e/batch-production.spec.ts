import { expect, test, type APIRequestContext } from "@playwright/test";

async function fixture(request: APIRequestContext) {
  const create = async (url: string, data: object) => {
    const response = await request.post(url, { data });
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  };
  const account = await create("/api/creators", { display_name: "批量生产隔离验收" });
  const series = await create(`/api/creators/${account.id}/series`, { name: "批量生产测试栏目" });
  const queued = await create(`/api/series/${series.id}/queue`, { request_id: crypto.randomUUID(),
    topics: [{ title: "第一条等待后台生产" }, { title: "第二条自动接着生产" }, { title: "第三条暂不生产" }] });
  return { account, series, topicIds: queued.topic_ids as string[] };
}

test("batch selection creates durable runs through the real isolated API and refresh never resubmits", async ({ page, request }, info) => {
  // Isolated SQLite + deterministic E2EProducer, not a real Codex generation claim.
  const data = await fixture(request);
  const posted: string[][] = [];
  page.on("request", req => { if (req.method() === "POST" && new URL(req.url()).pathname === "/api/runs/batch") posted.push(req.postDataJSON().topic_ids); });
  await page.goto(`/?creator=${data.account.id}&series=${data.series.id}`);
  const all = page.getByRole("checkbox", { name: "全选本页可生产选题" });
  await all.check();
  await expect(page.getByRole("button", { name: "批量生产（3）", exact: true })).toBeEnabled();
  await page.getByRole("checkbox", { name: "选择生产 第三条暂不生产", exact: true }).uncheck();
  await expect(all).not.toBeChecked();
  await expect.poll(() => all.evaluate((el: HTMLInputElement) => el.indeterminate)).toBe(true);
  await page.screenshot({ path: info.outputPath("batch-selection-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("batch-selection-mobile.png"), fullPage: true });
  await page.getByRole("button", { name: "批量生产（2）", exact: true }).click();
  const result = page.getByRole("region", { name: "批量提交结果" });
  await expect(result).toContainText("已受理 2 项");
  await expect(result.getByRole("link")).toHaveCount(2);
  expect(posted).toEqual([data.topicIds.slice(0, 2)]);
  const runs = await request.get(`/api/series/${data.series.id}/topics`).then(r => r.json());
  expect(runs.items.slice(0, 2).every((topic: { existing_run_id: string | null }) => topic.existing_run_id)).toBe(true);
  expect(runs.items[2].existing_run_id).toBeNull();
  await page.reload();
  await expect(page.getByRole("heading", { name: "批量生产测试栏目", exact: true })).toBeVisible();
  expect(posted).toHaveLength(1);
  await expect.poll(async () => {
    const topics = await request.get(`/api/series/${data.series.id}/topics`).then(r => r.json());
    return topics.items.slice(0, 2).map((topic: { existing_run_status: string }) => topic.existing_run_status);
  }, { timeout: 20_000 }).toEqual(["awaiting_approval", "awaiting_approval"]);
  await page.getByRole("button", { name: "刷新选题库" }).click();
  await expect(page.getByTestId(`library-${data.topicIds[0]}`)).toContainText("待批准");
  await expect(page.getByTestId(`library-${data.topicIds[1]}`)).toContainText("待批准");
  await expect(page.getByRole("checkbox", { name: "选择生产 第三条暂不生产", exact: true })).not.toBeChecked();
  expect(posted).toHaveLength(1);
});

test("partial rejection preserves only rejected selections and HTTP failure does not auto-retry", async ({ page, request }, info) => {
  const data = await fixture(request);
  let posts = 0;
  // Controlled response rejection tests UI handling, not producer reliability.
  await page.route("**/api/runs/batch", async route => {
    posts++;
    if (posts > 1) return route.fulfill({ status: 503, json: { error: { message: "受控队列暂不可用" } } });
    await route.fulfill({ status: 200, json: { accepted_count: 1, items: [
      { topic_id: data.topicIds[0], accepted: true, status: "queued", run_id: "controlled-run-link" },
      { topic_id: data.topicIds[1], accepted: false, status: "rejected", error: "受控拒绝：栏目配置有变化" },
      { topic_id: data.topicIds[2], accepted: false, status: "rejected", error: "受控拒绝：请检查选题" },
    ] } });
  });
  await page.goto(`/?creator=${data.account.id}&series=${data.series.id}`);
  await page.getByRole("checkbox", { name: "全选本页可生产选题" }).check();
  await page.getByRole("button", { name: "批量生产（3）", exact: true }).click();
  await expect(page.getByRole("region", { name: "批量提交结果" })).toContainText("受控拒绝：栏目配置有变化");
  await expect(page.getByRole("checkbox", { name: "选择生产 第一条等待后台生产", exact: true })).not.toBeChecked();
  await expect(page.getByRole("checkbox", { name: "选择生产 第二条自动接着生产", exact: true })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: "选择生产 第三条暂不生产", exact: true })).toBeChecked();
  await page.getByRole("button", { name: "批量生产（2）", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("受控队列暂不可用");
  await expect(page.getByRole("checkbox", { name: "选择生产 第二条自动接着生产", exact: true })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: "选择生产 第三条暂不生产", exact: true })).toBeChecked();
  expect(posts).toBe(2);
  await page.screenshot({ path: info.outputPath("batch-partial-error-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("batch-partial-error-mobile.png"), fullPage: true });
  await page.getByRole("button", { name: "待选", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: /选择生产/ })).toHaveCount(0);
  await page.getByRole("button", { name: "全部", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "选择生产 第二条自动接着生产", exact: true })).not.toBeChecked();
  await page.reload();
  await expect(page.getByRole("heading", { name: "批量生产测试栏目", exact: true })).toBeVisible();
  expect(posts).toBe(2);
});

test("durable queue projection distinguishes waiting and attention; blocked worker never looks active", async ({ page, request }) => {
  const data = await fixture(request);
  const created = await request.post("/api/runs", { data: { topic_id: data.topicIds[0] } }).then(r => r.json());
  const writes: string[] = [];
  page.on("request", req => { if (req.method() !== "GET") writes.push(new URL(req.url()).pathname); });
  // Controlled read projections cover queue states without executing or publishing.
  // Capture the real DTO once; repeated poll intercepts must not keep an HTTP
  // request alive past page teardown (which disposes route.fetch responses).
  const library = await request.get(`/api/series/${data.series.id}/topic-library?state=all&offset=0&limit=20`).then(r => r.json());
  library.items[0] = { ...library.items[0], existing_run_id: created.id, production_queue_status: "pending", production_queue_position: 2, available_actions: [] };
  library.items[1] = { ...library.items[1], production_queue_status: "needs_attention", production_queue_position: null, available_actions: ["resume"] };
  await page.route(`**/api/series/${data.series.id}/topic-library?**`, async route => {
    await route.fulfill({ json: library });
  });
  await page.goto(`/?creator=${data.account.id}&series=${data.series.id}`);
  await expect(page.getByTestId(`library-${data.topicIds[0]}`)).toContainText("排队中");
  await expect(page.getByTestId(`library-${data.topicIds[0]}`)).toContainText("第 2 项");
  await expect(page.getByTestId(`library-${data.topicIds[0]}`).getByRole("button", { name: "生产", exact: true })).toHaveCount(0);
  await expect(page.getByTestId(`library-${data.topicIds[1]}`)).toContainText("待核对");
  await expect(page.getByRole("checkbox", { name: /选择生产/ })).toHaveCount(1);
  await expect(page.getByTestId(`library-${data.topicIds[1]}`).getByRole("button", { name: "恢复生产", exact: true })).toBeVisible();
  await page.route(`**/api/runs/${created.id}`, async route => {
    await route.fulfill({ json: { ...created, production_queue: { status: "pending", position: 2,
      submitted_at: new Date().toISOString(), blocked_reason: "受控暂停：Codex 环境未就绪" } } });
  });
  await page.goto(`/runs/${created.id}`);
  await expect(page.getByText("生产队列已暂停 · 受控暂停：Codex 环境未就绪")).toBeVisible();
  await expect(page.getByRole("button", { name: "开始生产", exact: true })).toHaveCount(0);
  expect(writes).toEqual([]);
});
