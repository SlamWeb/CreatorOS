import { expect, test } from "@playwright/test";
import { ensureBuiltinImageSkill } from "./fixtures/image-default";

test("product actions retain form, conflict, navigation and download behavior", async ({ page }, testInfo) => {
  // Bound Skills + a controlled producer in isolated SQLite, never a paid model.
  const setup = await page.request.post("/test/calibration", { data: {} });
  expect(setup.ok()).toBeTruthy();
  const { series_id, creator_id } = await setup.json();
  const skill = await ensureBuiltinImageSkill(page.request, creator_id);
  await page.goto(`/?series=${series_id}`);
  await page.getByRole("button", { name: "添加选题", exact: true }).click();
  await page.getByLabel("新选题标题").fill("UI 控件迁移验收");
  await page.getByRole("button", { name: "添加", exact: true }).click();
  await expect(page.getByRole("heading", { name: "UI 控件迁移验收", exact: true })).toBeVisible();
  const topics = await (await page.request.get(`/api/series/${series_id}/topics`)).json();
  const topic = topics.items.find((item: { title: string }) => item.title === "UI 控件迁移验收");
  const creation = await page.request.post("/api/runs", { data: { topic_id: topic.id, request_id: crypto.randomUUID(),
    skill_overrides: { skill_id: skill.id, expected_digests: { [skill.id]: skill.digest } },
  } });
  expect(creation.ok(), await creation.text()).toBeTruthy();
  const run = await creation.json();
  const start = await page.request.post(`/api/runs/${run.id}/execute`, { data: { expected_version: run.version } });
  expect(start.ok()).toBeTruthy();
  await page.goto(`/runs/${run.id}`);
  const runUrl = page.url();
  await expect(page.getByRole("button", { name: "批准第 1 版" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("button", { name: "批准第 1 版" })).toHaveCSS("background-color", "rgb(0, 113, 227)");
  await page.screenshot({ path: testInfo.outputPath("product-desktop.png"), fullPage: true });

  await page.getByRole("button", { name: "提出返工", exact: true }).click();
  const instructions = page.getByLabel("告诉生产者要改哪里");
  await expect(instructions).toBeFocused();
  await expect(page.getByRole("button", { name: "保存返工要求" })).toBeDisabled();
  await instructions.fill("保留第一张，只调整第二张。");
  await page.route("**/api/runs/*/revisions", route => route.fulfill({
    status: 409, json: { error: { message: "受控冲突：页面版本已过期" } },
  }), { times: 1 });
  await page.getByRole("button", { name: "保存返工要求" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "内容已变化" })).toBeVisible();
  await expect(instructions).toHaveValue("保留第一张，只调整第二张。");
  await expect(page.getByRole("button", { name: "批准第 1 版" })).toBeDisabled();
  await page.getByRole("button", { name: "重新检查", exact: true }).click();
  await expect(page.getByRole("button", { name: "批准第 1 版" })).toBeEnabled();
  await page.getByRole("button", { name: "收起", exact: true }).click();

  await page.getByRole("button", { name: "更多", exact: true }).click();
  await expect(page.getByRole("button", { name: "删除该条内容", exact: true })).toHaveCSS("color", "rgb(196, 66, 59)");
  await page.getByRole("button", { name: "删除该条内容", exact: true }).click();
  const confirmation = page.getByRole("group", { name: "确认删除内容" });
  await expect(confirmation).toBeVisible();
  await expect(confirmation.getByRole("button", { name: "确认删除", exact: true })).toHaveCSS("color", "rgb(196, 66, 59)");
  await confirmation.getByRole("button", { name: "取消", exact: true }).click();
  await expect(confirmation).toHaveCount(0);

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("button", { name: "提出返工", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({ path: testInfo.outputPath("product-mobile.png"), fullPage: true });
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载全部（3）", exact: true }).click();
  expect((await download).suggestedFilename()).toMatch(/\.zip$/);
  await page.reload();
  await expect(page.getByRole("button", { name: "批准第 1 版" })).toBeVisible();
  await page.getByRole("link", { name: "返回栏目", exact: false }).click();
  await expect(page.getByRole("region", { name: "选题库" })).toBeVisible();
  expect(new URL(page.url()).searchParams.get("series")).toBe(series_id);
  await page.goto(runUrl);
  await expect(page.getByRole("button", { name: "批准第 1 版" })).toBeVisible();
  const failedRead = async (route: import("@playwright/test").Route) => route.fulfill({
    status: 503, json: { error: { message: "受控服务暂不可用" } },
  });
  await page.route(`**/api/runs/${run.id}`, failedRead);
  await page.reload();
  await expect(page.getByRole("button", { name: "重新连接", exact: true })).toBeVisible();
  await page.unroute(`**/api/runs/${run.id}`, failedRead);
  await page.getByRole("button", { name: "重新连接", exact: true }).click();
  await expect(page.getByRole("button", { name: "批准第 1 版" })).toBeVisible();
  await page.getByRole("button", { name: "提出返工", exact: true }).click();
  await instructions.fill("保留第一张，只调整第二张。");
  await page.getByRole("button", { name: "保存返工要求" }).click();
  await expect(page.getByRole("button", { name: "开始生产", exact: true })).toBeVisible();
  const saved = await (await page.request.get(`/api/runs/${run.id}`)).json();
  expect(saved.revisions).toHaveLength(2);
  expect(saved.revisions[0].cards).toHaveLength(3);
  expect(saved.revisions[1].instruction).toBe("保留第一张，只调整第二张。");
  expect(saved.revisions[1].attempts).toHaveLength(0);
  await page.getByRole("button", { name: "开始生产", exact: true }).click();
  await page.getByRole("button", { name: "批准第 2 版" }).click();
  await expect(page.getByText("已批准 · 尚未发布", { exact: true })).toBeVisible();
  expect((await (await page.request.get(`/api/runs/${run.id}`)).json()).status).toBe("approved");
  await page.reload();
  await expect(page.getByText("已批准 · 尚未发布", { exact: true })).toBeVisible();
});
