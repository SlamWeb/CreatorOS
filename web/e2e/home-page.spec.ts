import { expect, test } from "@playwright/test";

test("entry home links to real workspaces and keeps old deep links", async ({ page }, testInfo) => {
  const setup = await page.request.post("/test/calibration", { data: {} });
  expect(setup.ok()).toBeTruthy();
  const fixture = await setup.json();
  const writes: string[] = [];
  page.on("request", req => { if (req.method() !== "GET" && req.url().includes("/api/")) writes.push(req.url()); });
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: /每个好作品，\s*都不止于一次。/ })).toBeVisible();
  await expect(page.locator(".home-hero > p")).toHaveText("把这一次的创作经验，变成下一次的起点。");
  await expect(page.locator(".home-hero h1")).toHaveCSS("color", "rgb(29, 29, 31)");
  await expect(page.getByRole("link", { name: "进入工作台", exact: true })).toHaveCount(1);
  await expect(page.locator(".home-page svg")).toHaveCount(1);
  await expect(page.locator(".home-page footer")).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "主导航" })).toHaveCount(0);
  const account = page.locator(".home-account").filter({ hasText: "[隔离验证] 校准账号" });
  await expect(account).toBeVisible();
  await expect(account).toHaveAttribute("href", `/?creator=${fixture.creator_id}`);
  const primary = page.locator(".home-actions").getByRole("link", { name: "进入工作台" });
  const secondary = page.getByRole("navigation", { name: "首页导航" }).getByRole("link", { name: "Skill 库", exact: true });
  await expect(primary).toHaveCSS("background-color", "rgb(29, 29, 31)");
  await primary.hover();
  await expect(primary).toHaveCSS("background-color", "rgb(54, 54, 56)");
  await secondary.hover();
  await expect(secondary).toHaveCSS("text-decoration-line", "none");
  await primary.focus();
  await expect(primary).toBeFocused();
  await page.locator("#home-title").click();
  await page.screenshot({ path: testInfo.outputPath("home-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 2550, height: 1292 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(2550);
  await page.screenshot({ path: testInfo.outputPath("home-wide.png"), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 900 });
  expect(writes).toEqual([]);
  await primary.press("Enter");
  await expect(page).toHaveURL(/\/workspace$/);
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
  await page.getByRole("link", { name: "CreatorOS 首页", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: /每个好作品/ })).toBeVisible();
  await account.click();
  expect(new URL(page.url()).searchParams.get("creator")).toBe(fixture.creator_id);
  await expect(page.getByRole("navigation", { name: "账号工作区" })).toBeVisible();
  await page.goto(`/?series=${fixture.series_id}`);
  await expect(page.getByRole("heading", { level: 1, name: "[隔离验证] 故事校准" })).toBeVisible();
  await page.goto(`/runs/${fixture.run_id}`);
  await expect(page.getByRole("region", { name: "验收与返工" })).toBeVisible();
  await page.goto("/");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(primary).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({ path: testInfo.outputPath("home-mobile.png"), fullPage: true });
  await secondary.click();
  await expect(page).toHaveURL(/\/skills$/);
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
  const evidence = await (await page.request.get("/test/calibration-evidence")).json();
  expect(evidence.protected_records).toEqual(fixture.baseline_records);
});

test("empty and unavailable accounts never block entry or fake content", async ({ page }, testInfo) => {
  await page.route("**/api/creators?limit=4", route => route.fulfill({
    status: 200, json: { items: [], page: { total: 0, limit: 4, offset: 0 } },
  }));
  await page.goto("/");
  await expect(page.getByText("你的第一个创作账号，从这里开始。", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "创建账号", exact: false }).click();
  await expect(page).toHaveURL(/\/workspace$/);
  await page.unroute("**/api/creators?limit=4");
  await page.route("**/api/creators?limit=4", route => route.fulfill({
    status: 503, json: { error: { message: "受控故障：账号读取失败" } },
  }));
  await page.goto("/");
  await expect(page.getByText("暂时无法读取账号，你仍可以进入工作台。", { exact: true })).toBeVisible();
  await expect(page.locator(".home-account")).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("home-read-error.png"), fullPage: true });
  await page.unroute("**/api/creators?limit=4");
  await page.getByRole("button", { name: "重新读取", exact: true }).click();
  await expect(page.locator(".home-account").first()).toBeVisible();
});
