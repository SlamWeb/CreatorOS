import { expect, test } from "@playwright/test";

// Free locator preflight: original SPA + real isolated SQLite/HTTP, no model.
test("Skill composer can select the original account dropdown", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "+ 新账号" }).click();
  await page.getByLabel("新账号名称").fill("定位预检账号");
  await page.getByLabel("新账号标识").fill("locator_preflight");
  await page.getByRole("button", { name: "创建", exact: true }).click();
  // Creation refreshes the tree; an existing workspace selection is preserved.
  await page.getByRole("button", { name: "查看账号 定位预检账号", exact: true }).click();
  await expect(page.getByRole("heading", { name: "定位预检账号", level: 1, exact: true })).toBeVisible();
  await page.goto("/skills");
  const composer = page.getByRole("region", { name: "组合栏目", exact: true });
  const account = composer.getByLabel("归属账号");
  await expect(account).toHaveCount(1);
  await account.selectOption({ label: "定位预检账号" }, { timeout: 5000 });
  await expect(account.locator("option:checked")).toHaveText("定位预检账号");
});
