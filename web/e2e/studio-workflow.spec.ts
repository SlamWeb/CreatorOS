import { expect, test } from "@playwright/test";

test("first use to revision and approval survives refresh", async ({ page }, testInfo) => {
  await page.goto("/");
  await expect(page.getByText("还没有运营账号")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("01-first-use.png"), fullPage: true });

  await page.getByRole("link", { name: /创建第一个账号/ }).click();
  await page.getByRole("button", { name: /创建账号/ }).click();
  await page.getByLabel("账号名称").fill("E2E 知识实验室");
  await page.getByLabel(/账号标识/).fill("e2e_lab");
  await page.getByRole("button", { name: "保存账号" }).click();
  await expect(page.getByRole("heading", { name: "E2E 知识实验室" })).toBeVisible();

  await page.getByRole("button", { name: "创建栏目" }).click();
  await page.getByLabel("栏目名称").fill("Agent 每日一题");
  await page.getByLabel("目标受众").fill("准备 Agent 面试的开发者");
  await page.getByLabel("栏目定位").fill("用图片讲明白一个 Agent 工程知识点");
  await page.getByRole("button", { name: "保存栏目" }).click();
  await expect(page.getByRole("heading", { name: "Agent 每日一题" })).toBeVisible();

  await page.getByLabel(/添加选题/).fill("Agent State 和 Messages 有什么区别？");
  await page.getByRole("button", { name: "生成 Preview" }).click();
  const dialog = page.getByRole("dialog", { name: "运营指令" });
  await expect(dialog.getByText("等待确认")).toBeVisible();

  const operationId = new URL(page.url()).searchParams.get("operation");
  expect(operationId).toBeTruthy();
  await page.evaluate(async () => {
    const seriesId = location.pathname.split("/").at(-1)!;
    const replacement = await fetch("/api/operations/preview", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        request_text: "模拟另一页面先修改队列", series_id: seriesId,
        plan: { schema_version: 1, operations: [{ action: "add_topics", series_id: seriesId, topics: [{ topic_id: "e2e-stale-winner", title: "Agent State 和 Messages 有什么区别？", source: "manual" }] }] },
      }),
    }).then((response) => response.json());
    await fetch(`/api/operations/${replacement.id}/confirm`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_version: replacement.version, expected_revision: replacement.revision, confirmation_token: replacement.confirmation_token }),
    });
  });
  await dialog.getByRole("button", { name: "确认写入队列" }).click();
  await expect(dialog.getByText(/计划已在其他页面更新|内容已变化/)).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("02-stale-confirmation.png"), fullPage: true });
  await dialog.getByRole("button", { name: "重新查看最新计划" }).click();
  await expect(dialog.getByText("队列已变化")).toBeVisible();
  await dialog.getByRole("button", { name: /关闭，稍后处理/ }).click();

  await page.getByRole("button", { name: "开始生产" }).click();
  await page.getByRole("link", { name: "运行" }).click();
  await page.getByRole("link", { name: /Agent State 和 Messages/ }).click();
  await expect(page.getByText(/Codex 正在生产/)).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("03-producing.png"), fullPage: true });
  await expect(page.getByRole("button", { name: "批准第 1 版" })).toBeVisible({ timeout: 15_000 });
  await page.screenshot({ path: testInfo.outputPath("04-inspector.png"), fullPage: true });

  await page.getByRole("button", { name: "提出返工" }).click();
  await page.getByLabel("告诉生产者要改哪里").fill("第二张换成点餐场景，保留其余结构。");
  await page.getByRole("button", { name: "保存返工要求" }).click();
  await page.getByRole("button", { name: "开始生产" }).click();
  await expect(page.getByRole("button", { name: "批准第 2 版" })).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: "批准第 2 版" }).click();
  await expect(page.getByText("✓ 已批准 · 尚未发布")).toBeVisible();
  await page.reload();
  await expect(page.getByText("✓ 已批准 · 尚未发布")).toBeVisible();

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "E2E 知识实验室" })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("05-home-with-data.png"), fullPage: true });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/runs");
  await page.getByRole("link", { name: /Agent State 和 Messages/ }).click();
  await expect(page.getByText("✓ 已批准 · 尚未发布")).toBeVisible();
  const width = await page.evaluate(() => ({ body: document.body.scrollWidth, viewport: window.innerWidth }));
  expect(width.body).toBeLessThanOrEqual(width.viewport);
  await page.screenshot({ path: testInfo.outputPath("06-mobile-approved.png"), fullPage: true });
});
