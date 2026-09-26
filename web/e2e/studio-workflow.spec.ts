import { expect, test } from "@playwright/test";

test("workspace first use to revision and approval survives refresh", async ({ page }, testInfo) => {
  await page.goto("/");
  await expect(page.getByText("还没有栏目")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("01-first-use.png"), fullPage: true });

  await page.getByRole("button", { name: "+ 新账号" }).click();
  await page.getByLabel("新账号名称").fill("E2E 知识实验室");
  await page.getByLabel("新账号标识").fill("e2e_lab");
  await page.getByRole("button", { name: "创建", exact: true }).click();
  await expect(page.getByRole("heading", { name: "E2E 知识实验室", level: 2 })).toBeVisible();

  await page.getByRole("button", { name: "在 E2E 知识实验室 下新建栏目" }).click();
  await page.getByLabel("新栏目名称").fill("Agent 每日一题");
  await page.getByRole("button", { name: "创建", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Agent 每日一题", level: 1 })).toBeVisible();

  await page.getByLabel("新选题标题").fill("Agent State 和 Messages 有什么区别？");
  await page.getByRole("button", { name: "添加", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Agent State 和 Messages 有什么区别？" })).toBeVisible();

  // Preview 冲突覆盖保留：一条计划先被别处确认，抽屉内确认应提示过期而不是覆盖。
  const seriesId = new URL(page.url()).searchParams.get("series")!;
  const first = await page.evaluate(async (id) => {
    return fetch("/api/operations/preview", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        request_text: "再添加一条选题", series_id: id,
        plan: { schema_version: 1, operations: [{ action: "add_topics", series_id: id, topics: [{ topic_id: "e2e-extra-topic", title: "什么是 Context 压缩？", source: "manual" }] }] },
      }),
    }).then((response) => response.json());
  }, seriesId);
  await page.evaluate(async (id) => {
    const replacement = await fetch("/api/operations/preview", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        request_text: "模拟另一页面先修改队列", series_id: id,
        plan: { schema_version: 1, operations: [{ action: "add_topics", series_id: id, topics: [{ topic_id: "e2e-stale-winner", title: "另一条抢先写入", source: "manual" }] }] },
      }),
    }).then((response) => response.json());
    await fetch(`/api/operations/${replacement.id}/confirm`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_version: replacement.version, expected_revision: replacement.revision, confirmation_token: replacement.confirmation_token }),
    });
  }, seriesId);
  await page.goto(`/?series=${seriesId}&operation=${first.id}`);
  const dialog = page.getByRole("dialog", { name: "运营指令" });
  await expect(dialog.getByText("等待确认")).toBeVisible();
  await dialog.getByRole("button", { name: "确认写入队列" }).click();
  await expect(dialog.getByText(/计划已在其他页面更新|内容已变化|确认凭证已过期/)).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("02-stale-confirmation.png"), fullPage: true });
  await page.keyboard.press("Escape");

  await page.getByRole("button", { name: "生产", exact: true }).first().click();
  await page.getByRole("link", { name: "查看本次运行" }).click();
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
  await expect(page.getByRole("button", { name: /Agent 每日一题/ })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("05-workspace-with-data.png"), fullPage: true });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByRole("button", { name: /Agent 每日一题/ })).toBeVisible();
  const width = await page.evaluate(() => ({ body: document.body.scrollWidth, viewport: window.innerWidth }));
  expect(width.body).toBeLessThanOrEqual(width.viewport);
  await page.screenshot({ path: testInfo.outputPath("06-mobile-workspace.png"), fullPage: true });

  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    for (const route of ["/", "/skills", "/agent"]) {
      await page.goto(route);
      await expect(page.locator("h1").first()).toBeVisible();
      await expect(page.locator(".studio-shell")).toHaveCSS("background-color", "rgb(255, 255, 255)");
      const overflow = await page.evaluate(() => [...document.querySelectorAll("main *")].filter(el => el.getBoundingClientRect().right > innerWidth + 1).map(el => ({ tag: el.tagName, cls: el.className, right: el.getBoundingClientRect().right })));
      expect(overflow, route).toEqual([]);
    }
  }
  await page.getByRole("button", { name: "看看我的账号和栏目 ↗" }).click();
  await expect(page.getByLabel("给 Agent 的消息")).toHaveValue("看看我有哪些账号和栏目，先不要生产。");
  await page.getByRole("button", { name: /运营指令/ }).click();
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  // Failure injection verifies readable feedback without making a model call.
  await page.route("**/api/agent/sessions", route => route.fulfill({ status: 503, json: { error: { message: "隔离验收：服务暂不可用" } } }));
  await page.reload();
  await expect(page.getByRole("alert")).toContainText("隔离验收：服务暂不可用", { timeout: 15_000 });
  await page.screenshot({ path: testInfo.outputPath("agent-error-mobile.png"), fullPage: true });
});
