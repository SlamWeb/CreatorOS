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
