import { expect, test } from "@playwright/test";

test("unpaid original P05 account/series/manual-topic locators", async ({ page, request }) => {
  test.skip(process.env.CREATOROS_WORKBENCH_READ_CASE !== "P05" || !!process.env.CREATOROS_WORKBENCH_READ_RUN_ID, "仅免费P05入队定位预检，不生产不打分");
  const scenario = await (await request.get("/__live_eval__/scenario")).json();
  await page.goto("/");
  await page.getByRole("button", { name: `查看账号 ${scenario.creator_name}`, exact: true }).click();
  await page.getByRole("button", { name: scenario.series_name, exact: true }).click();
  await expect(page.getByRole("heading", { name: scenario.series_name, level: 1 })).toBeVisible();
  await page.getByLabel("新选题标题", { exact: true }).fill(scenario.query);
  const queued = page.waitForResponse(r => r.request().method() === "POST" && new URL(r.url()).pathname.endsWith("/queue"));
  await page.getByRole("button", { name: "添加", exact: true }).click();
  expect((await queued).status()).toBe(201);
  const card = page.locator(".content-card").filter({ has: page.getByRole("heading", { name: scenario.query, exact: true }) });
  await expect(card).toBeVisible();
  await expect(card.getByRole("button", { name: "生产", exact: true })).toBeEnabled();
  // Deliberately do not click production; the separate host blocks all paid work.
  const after = (await (await request.get("/__live_eval__/result")).json()).case_id;
  expect(after).toBe("P05");
});
