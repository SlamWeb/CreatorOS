import { expect, test } from "@playwright/test";
import { conversationErrorAlerts, toolResultAlerts } from "./chat-alerts";

test("real account Agent: frozen steps → GUI turns → state evidence → Trace → Eval", async ({ page, request }, info) => {
  const scenarioResponse = await request.get("/__live_eval__/scenario");
  expect(scenarioResponse.ok()).toBe(true);
  const scenario = await scenarioResponse.json() as {
    case_id: string; run_id: string; creator_name: string; creator_id: string; query: string;
    steps: Array<{ kind: "user"; text: string } | { kind: "event"; name: string; details: string }>;
    variant?: string;
  };
  expect(scenario.case_id).toBe(info.project.name);
  const browser: Record<string, unknown> = { query: scenario.query, session_id: "", completed: false,
    steps: [], turn_posts: 0, session_posts: 0, posts_after_refresh: 0 };
  let refreshed = false;
  const steps = browser.steps as string[];
  const errors: string[] = [];
  async function event(name: string) {
    const accepted = await request.post("/__live_eval__/event", { data: { name, session_id: browser.session_id,
      variant: scenario.variant } });
    expect(accepted.status()).toBe(202);
    const { event_id: id } = await accepted.json();
    await expect.poll(async () => (await request.get(`/__live_eval__/event-result/${id}`)).status(),
      { timeout: 45_000 }).not.toBe(202);
    const response = await request.get(`/__live_eval__/event-result/${id}`);
    expect(response.ok(), await response.text()).toBe(true);
    steps.push(`显式控制事件：${name}（不是模型行动）`);
    return response.json();
  }
  async function refresh() {
    refreshed = true;
    await page.reload();
    await page.getByRole("button", { name: "打开账号对话" }).click();
    const panel = page.locator(".account-chat-panel");
    await expect(panel.locator(".chat-answer").last()).toHaveText(String(browser.visible_reply));
    browser.restored_reply = await panel.locator(".chat-answer").last().innerText();
    const key = `creatoros.agent.selected-chat.v1:creator%3A${scenario.creator_id}`;
    browser.restored_session_id = await page.evaluate(key => sessionStorage.getItem(key), key);
    steps.push("刷新同一会话：最终回复保持，未重新提交");
    refreshed = false;
  }
  page.on("pageerror", error => errors.push(error.message));
  page.on("request", event => {
    const url = new URL(event.url());
    if (event.method() !== "POST") return;
    if (url.pathname === "/api/agent/sessions") browser.session_posts = Number(browser.session_posts) + 1;
    if (url.pathname.endsWith("/turns")) browser.turn_posts = Number(browser.turn_posts) + 1;
    if (refreshed && url.pathname.startsWith("/api/agent/")) browser.posts_after_refresh = Number(browser.posts_after_refresh) + 1;
  });
  try {
    if (scenario.case_id === "E06") {
      const prepared = await event("prepare_interruption");
      browser.session_id = prepared.seeded_session_id;
      await event("reload_service");
      await event("replay_request");
      await page.addInitScript(({ key, id }) => sessionStorage.setItem(key, id), {
        key: `creatoros.agent.selected-chat.v1:creator%3A${scenario.creator_id}`, id: String(browser.session_id) });
    }
    if (scenario.case_id === "E09") await event("release_research_failure");
    await page.goto("/");
    steps.push("打开真实工作区");
    await page.getByRole("button", { name: `查看账号 ${scenario.creator_name}` }).click();
    steps.push("点击当前账号");
    await page.getByRole("button", { name: "打开账号对话" }).click();
    const panel = page.locator(".account-chat-panel");
    await expect(panel).toBeVisible();
    steps.push("打开账号浮动聊天");
    for (const step of scenario.steps) {
      if (step.kind === "event") {
        if (scenario.case_id === "E06" || (scenario.case_id === "E09" && step.name === "release_research_failure")
            || step.name === "concurrent_skill_edit") continue;
        const result = await event(step.name);
        if (step.name === "reload_service") await refresh();
        if (step.name === "change_current_data") {
          // Existing UI supplies the queue write. Audience mutation is a
          // separate declared controller event (there is no audience editor).
          await page.getByRole("button", { name: "关闭账号对话" }).click();
          await page.goto(`/series/${result.series_id}`);
          await page.getByRole("textbox", { name: "新选题标题" }).fill(result.ui_topic_title);
          const submitted = page.waitForResponse(response => response.request().method() === "POST"
            && new URL(response.url()).pathname.endsWith("/queue"));
          await page.getByRole("button", { name: "添加", exact: true }).click();
          expect((await submitted).ok()).toBe(true);
          steps.push("原前端新选题输入框真实入队；非 Agent 行动");
          await page.getByRole("button", { name: "打开账号对话" }).click();
        }
        continue;
      }
      await panel.getByRole("textbox", { name: "给 Agent 的消息" }).fill(step.text);
      const created = !browser.session_id ? page.waitForResponse(response => response.request().method() === "POST"
        && new URL(response.url()).pathname === "/api/agent/sessions") : null;
      const sent = page.waitForResponse(response => response.request().method() === "POST"
        && new URL(response.url()).pathname.endsWith("/turns"));
      await panel.getByRole("button", { name: "发送 ↑" }).click();
      steps.push(`原前端点击发送：${step.text}`);
      if (created) {
        const response = await created;
        expect(response.status()).toBe(201);
        browser.session_id = (await response.json()).id;
      }
      expect((await sent).status()).toBe(202);
    // Waiting on actual UI completion, not backend polling as the acceptance endpoint.
    await expect(panel.locator(".agent-composer [role=status]")).toContainText("可以继续对话", { timeout: scenario.case_id === "E08" ? 1_860_000 : 180_000 });
    await expect(conversationErrorAlerts(panel)).toHaveCount(0);
    const resultAlerts = toolResultAlerts(panel);
    if (scenario.case_id === "E09" && scenario.variant === "failed") {
      // Verify the error card was not hidden to make the evaluator pass.
      await expect(resultAlerts.first()).toBeVisible();
    }
    const alertEvidence = (browser.tool_result_alerts ??= []) as Array<{ query: string; texts: string[] }>;
    alertEvidence.push({ query: step.text, texts: await resultAlerts.allTextContents() });
    const final = panel.locator(".chat-answer").last();
    await expect(final).toBeVisible();
    browser.visible_reply = await final.innerText();
    expect(String(browser.visible_reply).trim().length).toBeGreaterThan(0);
    await panel.getByRole("button", { name: "复制回复原文" }).last().click();
    browser.copied_reply = await page.evaluate(() => navigator.clipboard.readText());
    steps.push("页面完整显示最终回复，点击复制取得完整原文");
    const checkpoint = await request.post("/__live_eval__/checkpoint", { data: {
      session_id: browser.session_id, query: step.text, visible_reply: browser.visible_reply,
      copied_reply: browser.copied_reply, completed: true } });
    expect(checkpoint.ok()).toBe(true);
    await page.screenshot({ path: info.outputPath(`turn-${(await checkpoint.json()).turn_index}-desktop.png`), fullPage: true });
    }
    await refresh();
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
    {
      let viewError = "";
      try {
        await page.setViewportSize({ width: 1440, height: 900 });
        await page.goto(`/eval?case=${scenario.case_id}&run=${report.run_id}`);
        await expect(page.getByRole("heading", { name: "运行结果" })).toBeVisible();
        await expect(page.getByRole("region", { name: "执行链路" })).toBeVisible();
        await expect(page.getByText("浏览器 E2E", { exact: true })).toBeVisible();
        await expect(page.getByRole("region", { name: "数据库预期与实际" })).toBeVisible();
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
      const viewBody = await view.text();
      await info.attach("view-confirmation-response.json", { body: JSON.stringify({
        status: view.status(), body: viewBody }, null, 2), contentType: "application/json" });
      expect(view.ok(), viewBody).toBe(true);
      const finalReport = JSON.parse(viewBody);
      await info.attach("final-report.json", { body: JSON.stringify(finalReport, null, 2), contentType: "application/json" });
      expect(viewError).toBe("");
      // A completed browser flow can reveal a failed model task. Preserve that
      // score and continue the full batch; do not retry for a passing answer.
      expect(["passed", "failed", "needs_review"]).toContain(finalReport.auto_status);
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.reload();
      await expect(page.getByRole("heading", { name: "运行结果" })).toBeVisible();
      await page.screenshot({ path: info.outputPath("eval-desktop.png"), fullPage: true });
    }
  }
});
