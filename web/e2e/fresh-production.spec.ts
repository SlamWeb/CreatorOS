import { expect, test, type APIRequestContext } from "@playwright/test";

async function post(request: APIRequestContext, path: string, data: object) {
  const response = await request.post(path, { data });
  expect(response.ok(), await response.text()).toBe(true);
  return response.json();
}

test("same topic starts clean once, retains original artifacts and survives a lost creation response", async ({ page, request }, info) => {
  const creator = await post(request, "/api/creators", { display_name: "干净重做隔离账号" });
  const series = await post(request, `/api/creators/${creator.id}/series`, { name: "干净重做" });
  const topics = await post(request, `/api/series/${series.id}/queue`, { request_id: crypto.randomUUID(), topics: [{ title: "保留选题，重新制作" }] });
  const original = await post(request, "/api/runs", { topic_id: topics.topic_ids[0] });
  await post(request, `/api/runs/${original.id}/execute`, { expected_version: original.version });
  await expect.poll(async () => (await request.get(`/api/runs/${original.id}`).then(r => r.json())).status).toBe("awaiting_approval");
  const before = await request.get(`/api/runs/${original.id}`).then(r => r.json());
  let createdId = "", loseResponse = true;
  const payloads: Record<string, unknown>[] = [];
  let executePosts = 0;
  page.on("request", req => { if (req.method() === "POST" && /\/api\/runs\/[^/]+\/execute$/.test(new URL(req.url()).pathname)) executePosts++; });
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
  await expect.poll(async () => (await request.get(`/api/runs/${createdId}`).then(r => r.json())).status).toBe("awaiting_approval");
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
