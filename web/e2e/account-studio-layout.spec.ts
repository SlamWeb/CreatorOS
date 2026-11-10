import { expect, test } from "@playwright/test";

test("account and series navigation stay in the workspace while global navigation remains stable", async ({ page, request }, info) => {
  const suffix = crypto.randomUUID().slice(0, 8);
  async function post(url: string, data: object) {
    const response = await request.post(url, { data });
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  }
  const first = await post("/api/creators", { display_name: `工作台甲-${suffix}` });
  const second = await post("/api/creators", { display_name: `工作台乙-${suffix}` });
  const one = await post(`/api/creators/${first.id}/series`, { name: `第一栏目-${suffix}` });
  const two = await post(`/api/creators/${first.id}/series`, { name: `第二栏目-${suffix}` });
  const other = await post(`/api/creators/${second.id}/series`, { name: `另一账号栏目-${suffix}` });
  await page.goto(`/workspace?creator=${first.id}&series=${one.id}`);
  const sidebar = page.getByRole("navigation", { name: "主导航" });
  await expect(sidebar.getByRole("link")).toHaveCount(5);
  await expect(page.getByRole("heading", { name: one.name })).toBeVisible();
  const box = await sidebar.boundingBox();
  await expect(page.getByRole("button", { name: "切换账号" })).toContainText(first.display_name);
  await page.getByRole("navigation", { name: "当前账号的栏目" }).getByRole("button", { name: two.name }).click();
  await expect(page.getByRole("heading", { name: two.name })).toBeVisible();
  await page.getByRole("button", { name: "可用 Skill" }).click();
  await expect(page).toHaveURL(/view=skills/);
  await page.getByRole("button", { name: "内容", exact: true }).click();
  await expect(page.getByRole("heading", { name: two.name })).toBeVisible();
  await sidebar.getByRole("link", { name: "Skill", exact: true }).click();
  expect(await sidebar.boundingBox()).toEqual(box);
  await sidebar.getByRole("link", { name: "账号与栏目" }).click();
  await expect(page.getByRole("heading", { name: two.name })).toBeVisible();
  await page.screenshot({ path: info.outputPath("workspace-desktop.png"), fullPage: true });
  await page.getByRole("button", { name: "切换账号" }).click();
  await page.getByRole("dialog", { name: "切换账号" }).getByRole("button", { name: `切换到账号 ${second.display_name}` }).click();
  await expect(page.getByRole("navigation", { name: "当前账号的栏目" }).getByRole("button", { name: other.name })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "当前账号的栏目" }).getByRole("button", { name: one.name })).toHaveCount(0);
  await page.goBack();
  await expect(page.getByRole("heading", { name: two.name })).toBeVisible();
  for (const width of [850, 390]) {
    await page.setViewportSize({ width, height: 844 });
    const before = await sidebar.boundingBox();
    await sidebar.getByRole("link", { name: "Skill", exact: true }).click();
    const after = await sidebar.boundingBox();
    expect(after?.x).toBe(before?.x);
    expect(after?.width).toBe(before?.width);
    await sidebar.getByRole("link", { name: "账号与栏目" }).click();
    await expect(page.getByRole("heading", { name: two.name })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  await page.screenshot({ path: info.outputPath("workspace-mobile.png"), fullPage: true });
});
