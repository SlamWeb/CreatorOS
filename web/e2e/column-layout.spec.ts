import { expect, test } from "@playwright/test";

test("column navigation remains usable on desktop and mobile", async ({ page, request }) => {
  const accountResponse = await request.post("/api/creators", { data: { display_name: `栏目布局-${crypto.randomUUID().slice(0, 8)}` } });
  expect(accountResponse.ok()).toBeTruthy();
  const account = await accountResponse.json();
  const seriesResponse = await request.post(`/api/creators/${account.id}/series`, { data: { name: "测试栏目" } });
  expect(seriesResponse.ok()).toBeTruthy();
  const series = await seriesResponse.json();
  await page.goto(`/workspace?creator=${account.id}&series=${series.id}`);
  await expect(page.getByRole("heading", { name: "测试栏目", exact: true })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Skill", exact: true })).toBeVisible();
  await expect(page.locator("main")).toHaveCount(1);
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "跳到正文" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#studio-main")).toBeFocused();
  await page.reload();
  await expect(page.getByRole("heading", { name: "测试栏目", exact: true })).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("link", { name: "Skill", exact: true })).toBeVisible();
  const toggle = page.getByRole("button", { name: /^账号与栏目 ·/ });
  await toggle.click();
  await expect(page.getByRole("button", { name: "测试栏目", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
