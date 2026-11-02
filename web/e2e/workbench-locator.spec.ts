import { expect, test } from "@playwright/test";

// Free locator preflight: original SPA + real isolated SQLite/HTTP, no model.
test("Skill composer can select an existing account", async ({ page, request }) => {
  const response = await request.post("/api/creators", { data: { display_name: `定位预检账号-${crypto.randomUUID().slice(0, 6)}` } });
  expect(response.ok(), await response.text()).toBeTruthy();
  const creator = await response.json();
  await page.goto("/skills");
  const composer = page.getByRole("region", { name: "组合栏目", exact: true });
  const account = composer.getByLabel("归属账号");
  await expect(account).toHaveCount(1);
  await account.selectOption(creator.id);
  await expect(account.locator("option:checked")).toHaveText(creator.display_name);
});
