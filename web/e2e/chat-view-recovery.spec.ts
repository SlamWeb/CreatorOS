import { expect, test } from "@playwright/test";

test("account panel keeps an unsaved session blocked, then reloads the old idle view", async ({ page, request }) => {
  const creatorResponse = await request.post("/api/creators", { data: { display_name: "保存故障隔离账号" } });
  expect(creatorResponse.status()).toBe(201);
  const creator = await creatorResponse.json() as { id: string };
  const sessionResponse = await request.post("/api/agent/sessions", { data: { creator_id: creator.id } });
  expect(sessionResponse.status()).toBe(201);
  const saved = await sessionResponse.json();
  let doc = { ...saved, title: "需核对的对话", version: 3, status: "interrupted", stream_revision: 9,
    view_persistence_pending: true,
    error: "计划续接记录保存失败，续接未开始。请检查存储后重启服务并刷新页面。" };
  let posts = 0;
  await page.route(`**/api/agent/sessions/${saved.id}`, route => route.fulfill({ json: doc }));
  await page.route(`**/api/agent/sessions/${saved.id}/turns`, route => {
    posts++;
    return route.abort();
  });
  await page.addInitScript(({ creatorId, sessionId }) => {
    sessionStorage.setItem(`creatoros.agent.selected-chat.v1:creator%3A${creatorId}`, sessionId);
  }, { creatorId: creator.id, sessionId: saved.id });
  await page.goto(`/workspace?creator=${creator.id}`);
  await page.getByRole("button", { name: "打开账号对话" }).click();
  const panel = page.locator(".account-chat-panel");
  await expect(panel.getByRole("alert").filter({ hasText: "续接未开始" })).toBeVisible();
  await panel.getByRole("textbox", { name: "给 Agent 的消息" }).fill("暂不发送");
  await expect(panel.getByRole("button", { name: "发送 ↑" })).toBeDisabled();
  doc = { ...saved, title: "保存故障前", version: 0, status: "idle", stream_revision: 1,
    view_persistence_pending: false, error: null };
  await page.reload();
  await expect(panel).toBeVisible();
  await expect(panel.getByRole("alert").filter({ hasText: "续接未开始" })).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "发送 ↑" })).toBeEnabled();
  expect(posts).toBe(0);
});
