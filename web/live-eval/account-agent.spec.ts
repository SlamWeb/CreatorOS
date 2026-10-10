import { expect, test } from "@playwright/test";

test("real account Agent: workspace → send → reply → reload → Trace → Eval", async ({ page, request }, info) => {
  const scenarioResponse = await request.get("/__live_eval__/scenario");
  expect(scenarioResponse.ok()).toBe(true);
  const scenario = await scenarioResponse.json() as {
    case_id: string; run_id: string; creator_name: string; creator_id: string; query: string;
  };
  expect(scenario.case_id).toBe(info.project.name);
  const browser: Record<string, unknown> = { query: scenario.query, session_id: "", completed: false,
    steps: [], turn_posts: 0, session_posts: 0, posts_after_refresh: 0 };
  let refreshed = false;
  const steps = browser.steps as string[];
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("request", event => {
    const url = new URL(event.url());
    if (event.method() !== "POST") return;
    if (url.pathname === "/api/agent/sessions") browser.session_posts = Number(browser.session_posts) + 1;
    if (url.pathname.endsWith("/turns")) browser.turn_posts = Number(browser.turn_posts) + 1;
    if (refreshed && url.pathname.startsWith("/api/agent/")) browser.posts_after_refresh = Number(browser.posts_after_refresh) + 1;
  });
  try {
    await page.goto("/");
    steps.push("打开真实工作区");
    await page.getByRole("button", { name: `查看账号 ${scenario.creator_name}` }).click();
    steps.push("点击当前账号");
    await page.getByRole("button", { name: "打开账号对话" }).click();
    const panel = page.locator(".account-chat-panel");
    await expect(panel).toBeVisible();
    steps.push("打开账号浮动聊天");
    await panel.getByRole("textbox", { name: "给 Agent 的消息" }).fill(scenario.query);
    const sessionCreated = page.waitForResponse(response => response.request().method() === "POST"
      && new URL(response.url()).pathname === "/api/agent/sessions");
    await panel.getByRole("button", { name: "发送 ↑" }).click();
    steps.push("点击发送（原前端 POST，未用 API 代发）");
    const created = await sessionCreated;
    expect(created.status()).toBe(201);
    browser.session_id = (await created.json()).id;
    // Waiting on actual UI completion, not backend polling as the acceptance endpoint.
    await expect(panel.getByRole("status")).toContainText("可以继续对话", { timeout: 180_000 });
    await expect(panel.getByRole("alert")).toHaveCount(0);
    const final = panel.locator(".chat-answer").last();
    await expect(final).toBeVisible();
    browser.visible_reply = await final.innerText();
    expect(String(browser.visible_reply).length).toBeGreaterThan(20);
    await panel.getByRole("button", { name: "复制回复原文" }).last().click();
    browser.copied_reply = await page.evaluate(() => navigator.clipboard.readText());
    steps.push("页面完整显示最终回复，点击复制取得完整原文");
    await page.screenshot({ path: info.outputPath("reply-desktop.png"), fullPage: true });
    refreshed = true;
    await page.reload();
    await page.getByRole("button", { name: "打开账号对话" }).click();
    await expect(page.locator(".account-chat-panel .chat-answer").last()).toHaveText(String(browser.visible_reply));
    browser.restored_reply = await page.locator(".account-chat-panel .chat-answer").last().innerText();
    const key = `creatoros.agent.selected-chat.v1:creator%3A${scenario.creator_id}`;
    browser.restored_session_id = await page.evaluate(key => sessionStorage.getItem(key), key);
    steps.push("刷新，再打开聊天：回复与会话仍为同一条，零重提");
    await page.locator(".account-chat-panel").getByRole("button", { name: "查看回复 Trace" }).last().click();
    const trace = page.getByRole("dialog", { name: "回复 Trace" });
    await expect(trace).toBeVisible();
    await expect(trace.getByRole("alert")).toHaveCount(0);
    await expect(trace.getByRole("navigation", { name: "模型请求步骤" }).getByRole("button").first()).toBeVisible();
    await expect(trace.getByRole("tab", { name: "上下文", exact: true })).toBeVisible();
    browser.trace_visible = true;
    steps.push("点击回复 Trace：真实请求快照可读取");
    await page.getByRole("button", { name: "关闭 Trace" }).click();
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: info.outputPath("reply-mobile.png"), fullPage: true });
    expect(errors).toEqual([]);
    browser.completed = true;
  } catch (error) {
    browser.error = error instanceof Error ? error.message : String(error);
    throw error;
  } finally {
    browser.page_errors = errors;
    // Collection endpoint does not send queries or supply fake tool output.
    const finish = await request.post("/__live_eval__/finish", { data: browser });
    expect(finish.status()).toBe(202);
    // Collection is asynchronous so its nested scoped reads cannot deadlock
    // against the production POST ownership lock. This never submits a query.
    await expect.poll(async () => (await request.get("/__live_eval__/result")).status(), { timeout: 30_000 }).toBe(200);
    const report = await (await request.get("/__live_eval__/result")).json() as { run_id: string; auto_status: string; execution_status: string };
    await info.attach("browser-run.json", { body: JSON.stringify({ ...browser, report }, null, 2), contentType: "application/json" });
    console.log(`${scenario.case_id}: /eval?case=${scenario.case_id}&run=${report.run_id}`);
    if (browser.completed) {
      expect(report.execution_status).toBe("completed");
      let viewError = "";
      try {
        await page.setViewportSize({ width: 1440, height: 900 });
        await page.goto(`/eval?case=${scenario.case_id}&run=${report.run_id}`);
        await expect(page.getByRole("heading", { name: "运行结果" })).toBeVisible();
        await expect(page.getByRole("region", { name: "执行链路" })).toBeVisible();
        await expect(page.getByText("浏览器 E2E", { exact: true })).toBeVisible();
        await expect(page.getByRole("region", { name: "数据库预期与实际" })).toContainText("无行内容变化");
        await page.setViewportSize({ width: 390, height: 844 });
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.screenshot({ path: info.outputPath("eval-mobile.png"), fullPage: true });
        await page.reload();
        await expect(page.getByRole("heading", { name: "运行结果" })).toBeVisible();
        expect(new URL(page.url()).searchParams.get("run")).toBe(report.run_id);
      } catch (error) {
        viewError = error instanceof Error ? error.message : String(error);
      }
      const view = await request.post("/__live_eval__/view", { data: { run_id: report.run_id,
        completed: !viewError, error: viewError, posts_after_refresh: browser.posts_after_refresh } });
      expect(view.ok()).toBe(true);
      const finalReport = await view.json();
      await info.attach("final-report.json", { body: JSON.stringify(finalReport, null, 2), contentType: "application/json" });
      expect(viewError).toBe("");
      expect(finalReport.auto_status).toBe("passed");
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.reload();
      await expect(page.getByRole("heading", { name: "运行结果" })).toBeVisible();
      await page.screenshot({ path: info.outputPath("eval-desktop.png"), fullPage: true });
    }
  }
});
