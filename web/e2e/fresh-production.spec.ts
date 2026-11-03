import { expect, test, type APIRequestContext } from "@playwright/test";
import { configureImageDefault } from "./fixtures/image-default";

async function post(request: APIRequestContext, path: string, data: object) {
  const response = await request.post(path, { data });
  expect(response.ok(), await response.text()).toBe(true);
  return response.json();
}

test("same topic starts clean once, retains original artifacts and survives a lost creation response", async ({ page, request }, info) => {
  const creator = await post(request, "/api/creators", { display_name: "干净重做隔离账号" });
  const series = await post(request, `/api/creators/${creator.id}/series`, { name: "干净重做" });
  await configureImageDefault(request, creator.id, series.id);
  const topics = await post(request, `/api/series/${series.id}/queue`, { request_id: crypto.randomUUID(), topics: [{ title: "保留选题，重新制作" }] });
  const original = await post(request, "/api/runs", { topic_id: topics.topic_ids[0] });
  await post(request, `/api/runs/${original.id}/execute`, { expected_version: original.version });
  await expect.poll(async () => (await request.get(`/api/runs/${original.id}`).then(r => r.json())).status, { timeout: 30_000 }).toBe("awaiting_approval");
  const before = await request.get(`/api/runs/${original.id}`).then(r => r.json());
  let createdId = "", loseResponse = true;
  const payloads: Record<string, unknown>[] = [];
  let executePosts = 0;
  const executePayloads: Record<string, unknown>[] = [];
  page.on("request", req => { if (req.method() === "POST" && /\/api\/runs\/[^/]+\/execute$/.test(new URL(req.url()).pathname)) {
    executePosts++; executePayloads.push(req.postDataJSON());
  } });
  await page.route("**/api/runs", async route => {
    if (route.request().method() !== "POST") return route.continue();
    payloads.push(route.request().postDataJSON());
    const response = await route.fetch();
    expect(response.ok(), await response.text()).toBe(true);
    createdId = (await response.json()).id;
    if (loseResponse) { loseResponse = false; return route.abort("failed"); }
    return route.fulfill({ response });
  });
  await page.goto(`/runs/${original.id}`);
  await page.getByRole("button", { name: "从头重做", exact: true }).click();
  await expect(page.getByRole("group", { name: "确认从头重做" })).toContainText("最新 Skill 和全新对话");
  expect(payloads).toHaveLength(0);
  await page.getByRole("button", { name: "取消", exact: true }).click();
  expect(payloads).toHaveLength(0);
  await page.getByRole("button", { name: "从头重做", exact: true }).click();
  await page.getByRole("button", { name: "确认重做", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("不会自动重试");
  expect(payloads).toHaveLength(1);
  expect(executePosts).toBe(0);
  await page.getByRole("button", { name: "核对并继续同一请求", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/runs/${createdId}`));
  expect(createdId).not.toBe(original.id);
  expect(payloads).toHaveLength(2);
  expect(payloads[0]).toEqual(payloads[1]);
  expect(executePosts).toBe(1);
  expect(executePayloads[0].initial_only).toBe(true);
  await expect.poll(async () => (await request.get(`/api/runs/${createdId}`).then(r => r.json())).status, { timeout: 30_000 }).toBe("awaiting_approval");
  await page.reload();
  await expect(page.getByRole("button", { name: "批准第 1 版" })).toBeVisible();
  expect(executePosts).toBe(1);
  const fresh = await request.get(`/api/runs/${createdId}`).then(r => r.json());
  expect(fresh.topic_id).toBe(original.topic_id);
  expect(fresh.revisions).toHaveLength(1);
  expect(fresh.revisions[0].instruction).toBeNull();
  expect(fresh.revisions[0].attempts).toHaveLength(1);
  expect(fresh.publication).toBeNull();
  expect((await request.get(`/api/series/${series.id}/topics`).then(r => r.json())).items).toHaveLength(1);
  expect((await request.get(`/api/series/${series.id}/topics`).then(r => r.json())).items[0].existing_run_id).toBe(createdId);
  await page.goto(`/runs/${original.id}`);
  await expect(page.getByRole("link", { name: "查看最新制作" })).toHaveAttribute("href", new RegExp(createdId));
  await expect(page.getByRole("button", { name: "从头重做", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "删除该条内容", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "发布", exact: true })).toHaveCount(0);
  const old = await request.get(`/api/runs/${original.id}`).then(r => r.json());
  expect(old.revisions).toEqual(before.revisions);
  expect((await request.get(before.revisions[0].cards[0].url)).ok()).toBe(true);
  await page.getByRole("link", { name: "查看最新制作" }).click();
  await page.screenshot({ path: info.outputPath("fresh-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "从头重做", exact: true }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("fresh-mobile.png"), fullPage: true });
  expect(executePosts).toBe(1);
});

test("fresh replay shows attention and only explicit recovery submits without initial_only", async ({ page, request }) => {
  const creator = await post(request, "/api/creators", { display_name: "重做核对隔离账号" });
  const series = await post(request, `/api/creators/${creator.id}/series`, { name: "重做核对" });
  await configureImageDefault(request, creator.id, series.id);
  const topics = await post(request, `/api/series/${series.id}/queue`, { request_id: crypto.randomUUID(), topics: [{ title: "测试已存在重做任务" }] });
  const original = await post(request, "/api/runs", { topic_id: topics.topic_ids[0] });
  await post(request, `/api/runs/${original.id}/execute`, { expected_version: original.version });
  await expect.poll(async () => (await request.get(`/api/runs/${original.id}`).then(r => r.json())).status, { timeout: 30_000 }).toBe("awaiting_approval");
  const source = await request.get(`/api/runs/${original.id}`).then(r => r.json());
  const id = crypto.randomUUID();
  let fresh = { ...source, id, status: "queued", version: 1, active_revision_number: 1,
    allowed_actions: ["execute", "cancel"], production_queue: null, submission_accepted: null,
    revisions: [{ ...source.revisions[0], revision_number: 1, attempts: [], cards: [], artifact_available: false }] };
  const calls: Record<string, unknown>[] = [];
  await page.route("**/api/runs", route => route.request().method() === "POST"
    ? route.fulfill({ status: 201, json: fresh }) : route.continue());
  await page.route(`**/api/runs/${id}`, route => route.fulfill({ json: fresh }));
  await page.route(`**/api/runs/${id}/execute`, route => {
    calls.push(route.request().postDataJSON());
    fresh = { ...fresh, submission_accepted: calls.length === 1 ? false : null,
      production_queue: { status: calls.length === 1 ? "needs_attention" : "pending", position: null,
        submitted_at: new Date().toISOString(), last_error: "待核对" } };
    return route.fulfill({ status: 202, json: fresh });
  });
  await page.goto(`/runs/${original.id}`);
  await page.getByRole("button", { name: "从头重做", exact: true }).click();
  await page.getByRole("button", { name: "确认重做", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/runs/${id}`));
  await expect(page.getByText("任务状态需要核对；不会自动重新生产。" )).toBeVisible();
  expect(calls).toHaveLength(1);
  expect(calls[0].initial_only).toBe(true);
  await page.getByRole("button", { name: "恢复生产", exact: true }).click();
  await expect.poll(() => calls.length).toBe(2);
  expect(calls[1]).toEqual({ expected_version: 1 });
});

test("stale start button cannot resume a queue that now needs inspection", async ({ page, request }) => {
  const creator = await post(request, "/api/creators", { display_name: "过期开始按钮隔离账号" });
  const series = await post(request, `/api/creators/${creator.id}/series`, { name: "过期按钮" });
  await configureImageDefault(request, creator.id, series.id);
  const topics = await post(request, `/api/series/${series.id}/queue`, { request_id: crypto.randomUUID(), topics: [{ title: "过期状态" }] });
  const created = await post(request, "/api/runs", { topic_id: topics.topic_ids[0] });
  let current = await request.get(`/api/runs/${created.id}`).then(r => r.json());
  current = { ...current, production_queue: null, allowed_actions: ["execute", "cancel"] };
  let payload: Record<string, unknown> | undefined;
  await page.route(`**/api/runs/${created.id}`, route => route.fulfill({ json: current }));
  await page.route(`**/api/runs/${created.id}/execute`, route => {
    payload = route.request().postDataJSON();
    current = { ...current, submission_accepted: false,
      production_queue: { status: "needs_attention", position: null, submitted_at: new Date().toISOString(), last_error: "原错误" } };
    return route.fulfill({ status: 202, json: current });
  });
  await page.goto(`/runs/${created.id}`);
  await page.getByRole("button", { name: "开始生产", exact: true }).click();
  await expect(page.getByText("任务状态需要核对；不会自动重新生产。" )).toBeVisible();
  expect(payload).toEqual({ expected_version: created.version, initial_only: true });
  await expect(page.getByRole("button", { name: "恢复生产", exact: true })).toBeVisible();
});
