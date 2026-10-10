import { expect, test } from "@playwright/test";
import { conversationErrorAlerts, toolResultAlerts } from "../live-eval/chat-alerts";

test("live collector keeps failed tool cards and can send the next GUI query", async ({ page }) => {
  // Deterministic DOM/fault regression, not a real DeepSeek model score.
  await page.setContent(`<aside class="account-chat-panel">
    <div class="chat-tool" data-status="failed"><section class="research-activity">
      <p role="alert">调研失败：受控故障</p></section></div>
    <textarea aria-label="给 Agent 的消息"></textarea><button type="button">发送 ↑</button>
    <div class="chat-answer">失败已记录，请查询同一任务。</div></aside>`);
  const panel = page.locator(".account-chat-panel");
  await expect(toolResultAlerts(panel)).toHaveCount(1);
  await expect(toolResultAlerts(panel)).toBeVisible();
  await expect(conversationErrorAlerts(panel)).toHaveCount(0);
  await panel.getByRole("textbox", { name: "给 Agent 的消息" }).fill("只查原任务，不重新提交。");
  await panel.getByRole("button", { name: "发送 ↑" }).click();
  await expect(panel.getByRole("textbox")).toHaveValue("只查原任务，不重新提交。");
  await expect(toolResultAlerts(panel)).toBeVisible();
});

test("session and clipboard errors remain fatal even beside a failed tool card", async ({ page }) => {
  await page.setContent(`<aside class="account-chat-panel">
    <div class="chat-tool"><section class="research-activity"><p role="alert">调研失败</p></section></div>
    <p class="review-warning" role="alert">会话读取失败</p>
    <span class="chat-copy-error" role="alert">复制失败</span></aside>`);
  const panel = page.locator(".account-chat-panel");
  await expect(toolResultAlerts(panel)).toHaveCount(1);
  await expect(conversationErrorAlerts(panel)).toHaveCount(2);
});
