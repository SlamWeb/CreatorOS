import { expect, test, type APIRequestContext } from "@playwright/test";

async function post(request: APIRequestContext, url: string, data: object) {
  const response = await request.post(url, { data });
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}

test("one chronological feed ignores old filters, includes output and keeps page and return state", async ({ page, request }, info) => {
  const account = await post(request, "/api/creators", { display_name: `时间流-${crypto.randomUUID().slice(0, 6)}` });
  const series = await post(request, `/api/creators/${account.id}/series`, { name: "按时间排列" });
  const queued = await post(request, `/api/series/${series.id}/queue`, { request_id: crypto.randomUUID(),
    topics: Array.from({ length: 27 }, (_, i) => ({ title: `分组选题 ${i + 1}`, brief: "固定场景与内容，不启动真实模型。" })) });
  const run = await post(request, "/api/runs", { topic_id: queued.topic_ids[0] });
  const newest = await post(request, `/api/series/${series.id}/queue`, { request_id: crypto.randomUUID(),
    topics: [{ title: "最新添加的选题" }] });
  await post(request, `/api/runs/${run.id}/execute`, { expected_version: run.version });
  await expect.poll(async () => (await request.get(`/api/runs/${run.id}`).then(r => r.json())).status).toBe("awaiting_approval");
  const writes: string[] = [];
  page.on("request", r => { if (r.method() !== "GET") writes.push(new URL(r.url()).pathname); });
  const expected = await request.get(`/api/series/${series.id}/topic-library?order=created&limit=100`).then(r => r.json());
  await page.goto(`/?creator=${account.id}&series=${series.id}&topics=produced&order=queue`);
  await expect(page.getByRole("group", { name: "生产进度" })).toHaveCount(0);
  await expect(page.getByLabel("内容排序")).toHaveCount(0);
  await expect(page.locator(".content-card")).toHaveCount(20);
  await expect(page.locator(".library-count")).toHaveText("28 项");
  await expect(page.locator(".content-card").first()).toHaveAttribute("data-testid", `library-${newest.topic_ids[0]}`);
  expect(await page.locator(".content-card").evaluateAll(items => items.map(item => item.getAttribute("data-testid"))))
    .toEqual(expected.items.slice(0, 20).map((item: { id: string }) => `library-${item.id}`));
  await page.locator(".content-card-select").first().check();
  await expect(page.getByRole("group", { name: "批量生产" })).toBeVisible();
  await page.screenshot({ path: info.outputPath("chronological-desktop.png"), fullPage: false });
  await page.getByRole("navigation", { name: "选题分页 · 顶部" }).getByRole("button", { name: "下一页" }).click();
  await expect(page.getByRole("group", { name: "批量生产" })).toHaveCount(0);
  await expect(page.locator(".content-card")).toHaveCount(8);
  expect(await page.locator(".content-card").evaluateAll(items => items.map(item => item.getAttribute("data-testid"))))
    .toEqual(expected.items.slice(20).map((item: { id: string }) => `library-${item.id}`));
  const produced = page.getByTestId(`library-${queued.topic_ids[0]}`);
  await expect(produced.getByRole("img")).toBeVisible();
  await expect(produced.getByLabel("生产使用的 Skill")).toContainText("已用");
  await expect(produced.getByLabel("生产使用的 Skill")).toContainText("内置");
  await page.reload();
  await expect(page.locator(".content-card")).toHaveCount(8);
  await produced.getByRole("link", { name: "查看内容 分组选题 1", exact: true }).click();
  await page.getByRole("link", { name: "返回栏目", exact: true }).click();
  await expect(page).toHaveURL(/offset=20/);
  await expect(page.locator(`#topic-card-${queued.topic_ids[0]}`)).toBeFocused();
  await page.getByRole("navigation", { name: "选题分页 · 顶部" }).getByRole("button", { name: "上一页" }).click();
  await expect(page.locator(".content-card").first()).toHaveAttribute("data-testid", `library-${newest.topic_ids[0]}`);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("chronological-mobile.png"), fullPage: false });
  expect(writes).toEqual([]);
});

test("cards name exact task v1 and default v2; producing follows that default without switching the old task", async ({ page, request }, info) => {
  const suffix = crypto.randomUUID().slice(0, 6);
  const account = await post(request, "/api/creators", { display_name: `版本可见-${suffix}` });
  const draft = await post(request, "/api/skill-extractions/author", { request_id: crypto.randomUUID(),
    name: `card-layout-${suffix}`, display_name: "简约故事", description: "隔离测试，固定画面规格。",
    content: "制作清晰的单页故事图，不要额外背景物品。", role: "legacy_end_to_end", upload_ids: [] });
  const saved = await post(request, `/api/skill-extractions/${draft.id}/save`, { expected_digest: draft.digest });
  const first = saved.saved_skills[0];
  const created = await post(request, "/api/series", { name: "版本显示验证", creator_id: account.id,
    skill_name: first.id, request_id: crypto.randomUUID() });
  const sid = created.series.id;
  const queued = await post(request, `/api/series/${sid}/queue`, { request_id: crypto.randomUUID(),
    topics: [{ title: "已有任务沿用旧版" }, { title: "新任务使用栏目新版" }] });
  const old = await post(request, "/api/runs", { topic_id: queued.topic_ids[0] });
  const candidate = await post(request, `/api/producer-skills/${first.id}/versions`, {
    expected_digest: first.digest, request_id: crypto.randomUUID() });
  const second = await post(request, `/api/producer-skills/${candidate.id}/publish-version`, {
    expected_digest: candidate.digest, request_id: crypto.randomUUID() });
  const series = await request.get(`/api/series/${sid}`).then(r => r.json());
  await post(request, `/api/series/${sid}/skill-version`, { skill_id: second.id, expected_digest: second.digest,
    expected_revision: series.revision, request_id: crypto.randomUUID() });
  const writes: string[] = [];
  page.on("request", r => { if (r.method() !== "GET") writes.push(new URL(r.url()).pathname); });
  await page.goto(`/?creator=${account.id}&series=${sid}`);
  const previous = page.getByTestId(`library-${queued.topic_ids[0]}`);
  const next = page.getByTestId(`library-${queued.topic_ids[1]}`);
  await expect(previous.getByLabel("生产使用的 Skill")).toContainText("任务用");
  await expect(previous.getByLabel("生产使用的 Skill")).toContainText("v1");
  await expect(next.getByLabel("生产使用的 Skill")).toContainText("将用");
  await expect(next.getByLabel("生产使用的 Skill")).toContainText("v2");
  await page.screenshot({ path: info.outputPath("versions-desktop.png"), fullPage: false });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("versions-mobile.png"), fullPage: false });
  const started = page.waitForResponse(r => r.request().method() === "POST" && new URL(r.url()).pathname === "/api/runs");
  await next.getByRole("button", { name: "生产", exact: true }).click();
  const run = await (await started).json();
  expect(run.input_snapshot.skill_name).toBe(second.id);
  await expect.poll(async () => (await request.get(`/api/runs/${run.id}`).then(r => r.json())).status).toBe("awaiting_approval");
  await page.goto(`/?creator=${account.id}&series=${sid}`);
  const output = page.getByTestId(`library-${queued.topic_ids[1]}`);
  await expect(output.getByLabel("生产使用的 Skill")).toContainText("已用");
  await expect(output.getByLabel("生产使用的 Skill")).toContainText("v2");
  await output.getByLabel("生产使用的 Skill").getByRole("link").click();
  await expect(page).toHaveURL(new RegExp(`skill=${second.id}`));
  await expect(page.getByRole("dialog", { name: "简约故事", exact: true })).toBeVisible();
  const unchanged = await request.get(`/api/runs/${old.id}`).then(r => r.json());
  expect(unchanged.input_snapshot.skill_name).toBe(first.id);
  expect(writes).toEqual(["/api/runs", `/api/runs/${run.id}/execute`]);
});
