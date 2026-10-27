import { expect, test, type APIRequestContext } from "@playwright/test";

async function post(request: APIRequestContext, url: string, data: object) {
  const response = await request.post(url, { data });
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}

async function topicFixture(request: APIRequestContext, skillId?: string) {
  const suffix = crypto.randomUUID().slice(0, 6);
  const account = await post(request, "/api/creators", { display_name: `拆分测试-${suffix}` });
  const series = (await post(request, "/api/series", { creator_id: account.id, name: `多项制作-${suffix}`,
    skill_name: skillId ?? "knowledge-to-carousel", request_id: crypto.randomUUID() })).series;
  const queued = await post(request, `/api/series/${series.id}/queue`, { request_id: crypto.randomUUID(),
    topics: [{ title: `同一个故事-${suffix}`, brief: "隔离接线测试，不调用付费生产。" }] });
  return { account, series, topicId: queued.topic_ids[0], url: `/?creator=${account.id}&series=${series.id}` };
}

test("split creates two tasks with independent Skills in one topic, and refresh never resubmits", async ({ page, request }, info) => {
  const authored = await post(request, "/api/skill-extractions/author", { request_id: crypto.randomUUID(),
    name: `task-layout-${crypto.randomUUID().slice(0, 6)}`, display_name: "任务专用构图", description: "隔离制作任务测试。",
    content: "用清晰的单页图表达这个故事。", role: "legacy_end_to_end", upload_ids: [] });
  const saved = await post(request, `/api/skill-extractions/${authored.id}/save`, { expected_digest: authored.digest });
  const original = saved.saved_skills[0];
  const skill = await post(request, `/api/producer-skills/${original.id}/versions`, { expected_digest: original.digest, request_id: crypto.randomUUID() });
  const fixture = await topicFixture(request, original.id);
  const endpoint = `/api/topics/${fixture.topicId}/production-tasks`;
  const writes: { path: string; body: unknown }[] = [];
  page.on("request", value => { if (value.method() === "POST") writes.push({ path: new URL(value.url()).pathname, body: value.postDataJSON() }); });
  await page.goto(fixture.url);
  const card = page.getByTestId(`library-${fixture.topicId}`);
  await card.getByLabel(/选题操作/).click();
  await card.getByRole("button", { name: "拆分制作", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "选题详情" });
  await dialog.getByLabel("任务 1 名称", { exact: true }).fill("开场世界");
  await dialog.getByLabel("任务 1 制作要求", { exact: true }).fill("保持主角轮廓，画城市清晨。");
  await dialog.getByLabel("任务 2 名称", { exact: true }).fill("结尾世界");
  await dialog.getByLabel("任务 2 制作要求", { exact: true }).fill("保持主角轮廓，画森林黄昏。");
  const firstSkills = dialog.getByRole("group", { name: "任务 1 选择 Skill" });
  const secondSkills = dialog.getByRole("group", { name: "任务 2 选择 Skill" });
  await expect(firstSkills).toContainText(/v1/);
  await expect(secondSkills).toContainText(/v2.*候选/);
  await firstSkills.locator(`input[value="${original.id}"]`).check();
  await secondSkills.locator(`input[value="${skill.id}"]`).check();
  await dialog.evaluate(element => { element.scrollTop = 0; });
  await page.screenshot({ path: info.outputPath("split-desktop.png"), fullPage: true });
  await dialog.getByRole("button", { name: "提交并开始制作（2）", exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath("split-submit-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await dialog.evaluate(element => { element.scrollTop = 0; });
  await page.screenshot({ path: info.outputPath("split-mobile.png"), fullPage: true });
  await dialog.getByRole("button", { name: "提交并开始制作（2）", exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath("split-submit-mobile.png"), fullPage: true });
  expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.reload();
  await expect(dialog.getByLabel("任务 2 名称", { exact: true })).toHaveValue("结尾世界");
  expect(writes).toHaveLength(0);
  const submitted = page.waitForResponse(value => value.request().method() === "POST" && new URL(value.url()).pathname === endpoint);
  await dialog.getByRole("button", { name: "提交并开始制作（2）", exact: true }).click();
  const response = await submitted;
  expect(response.ok(), await response.text()).toBeTruthy();
  const result = await response.json();
  expect(result.items).toHaveLength(2);
  expect(result.items.map((item: { title: string }) => item.title)).toEqual(["开场世界", "结尾世界"]);
  await expect(dialog.getByRole("status").filter({ hasText: "已提交制作" })).toBeVisible();
  await expect(page.locator(".content-card")).toHaveCount(1);
  await expect(dialog.locator(".production-task-list > li")).toHaveCount(2);
  await expect(dialog.locator(".production-task-list > li").first()).toContainText("v1");
  await expect(dialog.locator(".production-task-list > li").nth(1)).toContainText("v2");
  expect(writes).toHaveLength(1);
  const body = writes[0].body as { tasks: { skill_ids: string[] }[] };
  expect(body.tasks[0].skill_ids).toEqual([original.id]);
  expect(body.tasks[1].skill_ids).toEqual([skill.id]);
  await page.reload();
  await expect(dialog.locator(".production-task-list > li")).toHaveCount(2);
  await expect(dialog.getByLabel("任务 1 名称", { exact: true })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await card.getByRole("button", { name: /查看选题/ }).click();
  await expect(dialog.locator(".production-task-list > li")).toHaveCount(2);
  await page.screenshot({ path: info.outputPath("tasks-mobile.png"), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.screenshot({ path: info.outputPath("tasks-desktop.png"), fullPage: true });
  await dialog.locator(".production-task-list > li").first().getByRole("link", { name: "查看任务" }).click();
  await expect(page).toHaveURL(new RegExp(`/runs/${result.items[0].run_id}`));
  await page.getByRole("link", { name: "返回栏目", exact: true }).click();
  await expect(dialog.locator(".production-task-list > li")).toHaveCount(2);
  await dialog.getByRole("link", { name: "查看素材集", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`collection=${result.collection_id}`));
  await expect(page.getByRole("region", { name: "素材文件夹", exact: true })).toBeVisible();
  expect(writes).toHaveLength(1);
  const library = await request.get(`/api/series/${fixture.series.id}/topic-library?limit=100`).then(value => value.json());
  expect(library.items).toHaveLength(1);
  expect(library.items[0].production_task_count).toBe(2);
});

test("lost task reply keeps one frozen submission across refresh and explicit reconciliation", async ({ page, request }) => {
  const fixture = await topicFixture(request);
  const endpoint = `/api/topics/${fixture.topicId}/production-tasks`;
  let attempts = 0;
  const bodies: unknown[] = [];
  // Controlled transport fault after the real HTTP/SQLite write; no mock claims to prove Codex production.
  await page.route(`**${endpoint}`, async route => {
    if (route.request().method() !== "POST") { await route.continue(); return; }
    attempts += 1; bodies.push(route.request().postDataJSON());
    if (attempts === 1) {
      const response = await route.fetch();
      expect(response.ok(), await response.text()).toBeTruthy();
      await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: { message: "受控：保存后响应丢失" } }) });
    } else await route.continue();
  });
  await page.goto(`${fixture.url}&topic=${fixture.topicId}&production=split`);
  const dialog = page.getByRole("dialog", { name: "选题详情" });
  await dialog.getByLabel("任务 1 名称", { exact: true }).fill("第一项保留");
  await dialog.getByLabel("任务 1 制作要求", { exact: true }).fill("先拍清晨的世界。");
  await dialog.getByLabel("任务 2 名称", { exact: true }).fill("第二项保留");
  await dialog.getByLabel("任务 2 制作要求", { exact: true }).fill("再拍黄昏的世界。");
  await dialog.getByRole("group", { name: "任务 1 选择 Skill" }).locator('input[value="knowledge-to-carousel"]').check();
  await dialog.getByRole("group", { name: "任务 2 选择 Skill" }).locator('input[value="knowledge-to-carousel"]').check();
  await dialog.getByRole("button", { name: "提交并开始制作（2）" }).click();
  await expect(dialog.getByRole("alert")).toContainText("提交结果待核对");
  await expect(dialog.getByLabel("任务 1 名称", { exact: true })).toBeDisabled();
  expect(attempts).toBe(1);
  await page.reload();
  await expect(dialog.locator(".production-task-list > li")).toHaveCount(2);
  await expect(dialog.getByLabel("任务 2 名称", { exact: true })).toHaveValue("第二项保留");
  await expect(dialog.getByLabel("任务 2 名称", { exact: true })).toBeDisabled();
  expect(attempts).toBe(1);
  await dialog.getByRole("button", { name: "核对同一提交", exact: true }).click();
  await expect(dialog.getByRole("status").filter({ hasText: "已提交制作" })).toBeVisible();
  expect(attempts).toBe(2);
  expect(bodies[0]).toEqual(bodies[1]);
  expect((await request.get(endpoint).then(value => value.json())).items).toHaveLength(2);
  await expect(page.locator(".content-card")).toHaveCount(1);
});

test("a task can explicitly select content and visual Skills without changing the series default", async ({ page, request }, info) => {
  const fixture = await topicFixture(request);
  const selected = await post(request, "/test/production-task-pair", {});
  // Installed Skills now require explicit account-library references before use.
  for (const skill of [selected.mind, selected.production]) await post(request, `/api/creators/${fixture.account.id}/skills`, { skill_id: skill.id, request_id: crypto.randomUUID() });
  const pool = await request.get(`/api/series/${fixture.series.id}/skills`).then(value => value.json());
  const updated = await request.put(`/api/series/${fixture.series.id}/skills`, { data: { expected_version: pool.version, skill_ids: ["knowledge-to-carousel", selected.mind.id, selected.production.id], defaults: pool.defaults } });
  expect(updated.ok(), await updated.text()).toBeTruthy();
  await page.goto(`${fixture.url}&topic=${fixture.topicId}&production=split`);
  const dialog = page.getByRole("dialog", { name: "选题详情" });
  await dialog.getByRole("button", { name: "移除任务 2", exact: true }).click();
  await dialog.getByLabel("任务 1 名称", { exact: true }).fill("组合制作");
  await dialog.getByLabel("任务 1 制作要求", { exact: true }).fill("以第一份组织内容，以第二份制作画面。");
  const skills = dialog.getByRole("group", { name: "任务 1 选择 Skill" });
  await skills.locator(`input[value="${selected.mind.id}"]`).check();
  await skills.locator(`input[value="${selected.production.id}"]`).check();
  await dialog.getByRole("button", { name: "提交并开始制作（1）", exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath("pair-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await dialog.getByRole("button", { name: "提交并开始制作（1）", exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath("pair-mobile.png"), fullPage: true });
  const submitted = page.waitForResponse(value => value.request().method() === "POST" && new URL(value.url()).pathname.endsWith("/production-tasks"));
  await dialog.getByRole("button", { name: "提交并开始制作（1）", exact: true }).click();
  const response = await submitted;
  expect(response.ok(), await response.text()).toBeTruthy();
  const result = await response.json();
  expect(result.items).toHaveLength(1);
  expect(response.request().postDataJSON().tasks[0].skill_ids).toEqual([selected.mind.id, selected.production.id]);
  const run = await request.get(`/api/runs/${result.items[0].run_id}`).then(value => value.json());
  expect(run.input_snapshot.execution_spec.skills.map((item: { skill_id: string }) => item.skill_id)).toEqual([selected.mind.id, selected.production.id]);
  const series = await request.get(`/api/series/${fixture.series.id}`).then(value => value.json());
  expect(series.skill_name).toBe("knowledge-to-carousel");
  expect(series.mind_skill_id).toBeNull();
  await expect(dialog.locator(".production-task-list > li")).toHaveCount(1);
});
