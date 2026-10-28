import { expect, test } from "@playwright/test";

test("计划展示步骤与依赖，失败可见并刷新保留", async ({ page, request }, info) => {
  const creatorResponse = await request.post("/api/creators", { data: { display_name: "计划界面隔离测试" } });
  expect(creatorResponse.ok()).toBeTruthy();
  const creator = await creatorResponse.json();
  const sessionResponse = await request.post("/api/agent/sessions", { data: { creator_id: creator.id } });
  expect(sessionResponse.ok()).toBeTruthy();
  const session = await sessionResponse.json();
  const path = `/api/agent/sessions/${session.id}/plans`;
  const response = await request.post(path, { data: { request_id: crypto.randomUUID(), title: "测试任务计划", steps: [
    { id: "read", title: "查看不存在的 Skill", tool: "get_producer_skill", arguments: { skill_id: "nonexistent-plan-test" } },
    { id: "next", title: "处理读取结果", tool: "get_producer_skill", arguments: { skill_id: "nonexistent-plan-test" }, depends_on: ["read"] },
  ] } });
  expect(response.ok(), await response.text()).toBeTruthy();
  const plan = await response.json();
  await page.goto(`/agent?creator=${creator.id}&chat=${session.id}`);
  const panel = page.getByRole("region", { name: "任务计划" });
  await expect(panel).toBeVisible();
  await panel.getByRole("button", { name: /任务计划/ }).click();
  await expect(panel).toContainText("测试任务计划");
  await expect(panel).toContainText("查看不存在的 Skill");
  await expect(panel).toContainText("处理读取结果");
  await expect(panel).toContainText("等待：查看不存在的 Skill");
  await expect.poll(async () => (await (await request.get(`${path}/${plan.id}`)).json()).steps[0].status).toBe("failed");
  await expect(panel).toContainText("失败", { timeout: 15_000 });
  await panel.getByRole("button", { name: "查看原因" }).first().click();
  await expect(panel.locator("pre").first()).toBeVisible();
  await page.screenshot({ path: info.outputPath("desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: info.outputPath("mobile.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.reload();
  await panel.getByRole("button", { name: /任务计划/ }).click();
  await expect(panel).toContainText("测试任务计划");
  await expect(panel).toContainText("失败");
});

test("关键节点等待 Agent 判断并在刷新后保留", async ({ page, request }, info) => {
  const creator = await (await request.post("/api/creators", { data: { display_name: "关键节点界面测试" } })).json();
  const session = await (await request.post("/api/agent/sessions", { data: { creator_id: creator.id } })).json();
  const path = `/api/agent/sessions/${session.id}/plans`;
  const response = await request.post(path, { data: { request_id: crypto.randomUUID(), title: "图片制作计划", steps: [
    { id: "review", title: "检查选中的图片", tool: "request_review", arguments: { question: "图片顺序是否合适？" } },
    { id: "next", title: "继续准备素材", tool: "get_producer_skill", arguments: { skill_id: "missing-plan-test" }, depends_on: ["review"] },
  ] } });
  expect(response.ok(), await response.text()).toBeTruthy();
  const plan = await response.json();
  await expect.poll(async () => (await (await request.get(`${path}/${plan.id}`)).json()).pause_reason).toBe("checkpoint");
  await page.goto(`/agent?creator=${creator.id}&chat=${session.id}`);
  const panel = page.getByRole("region", { name: "任务计划" });
  await expect(panel).toContainText("等待 Agent 判断");
  await panel.getByRole("button", { name: /任务计划/ }).click();
  await expect(panel).toContainText("检查选中的图片");
  await expect(panel.locator("li").filter({ has: page.locator("strong").getByText("检查选中的图片", { exact: true }) })).toContainText("等待判断");
  await expect(panel).toContainText("等待：检查选中的图片");
  await expect(panel.locator("li").filter({ hasText: "继续准备素材" }).getByRole("button", { name: "查看详情" })).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "继续计划" })).toHaveCount(0);
  await page.screenshot({ path: info.outputPath("checkpoint-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: info.outputPath("checkpoint-mobile.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.reload();
  await expect(panel).toContainText("等待 Agent 判断");
  const persisted = await (await request.get(`${path}/${plan.id}`)).json();
  expect(persisted.steps[0].status).toBe("succeeded");
  expect(persisted.steps[1].status).toBe("pending");
});
