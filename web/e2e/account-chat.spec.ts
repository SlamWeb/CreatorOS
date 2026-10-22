import { expect, test, type APIRequestContext } from "@playwright/test";

async function createCreator(request: APIRequestContext, name: string) {
  const response = await request.post("/api/creators", { data: { display_name: name } });
  expect(response.status()).toBe(201);
  return response.json() as Promise<{ id: string; display_name: string }>;
}

async function createSession(request: APIRequestContext, creatorId: string | null) {
  const response = await request.post("/api/agent/sessions", { data: { creator_id: creatorId } });
  expect(response.status()).toBe(201);
  return response.json() as Promise<{ id: string; creator_id: string | null; scope_kind: "overview" | "creator" }>;
}

test("the account launcher waits for the workspace owner instead of opening overview during loading", async ({ page, request }) => {
  const account = await createCreator(request, "延迟载入账号");
  let release!: () => void;
  const ready = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/creators", async route => { await ready; await route.continue(); });
  await page.goto(`/?creator=${account.id}`);
  const launcher = page.getByRole("button", { name: "打开账号对话" });
  await expect(launcher).toBeDisabled();
  release();
  await expect(launcher).toBeEnabled();
  await launcher.click();
  await expect(page.locator(".account-chat-panel").getByRole("heading", { name: account.display_name, exact: true })).toBeVisible();
});

test("account entry stays lazy and the first turn is bound to that account", async ({ page, request }, info) => {
  const creator = await createCreator(request, "账号聊天隔离验收");
  const otherCreator = await createCreator(request, "另一账号草稿隔离");
  let sessionPosts = 0;
  let creatorFilterReads = 0;
  page.on("request", requestEvent => {
    const url = new URL(requestEvent.url());
    if (requestEvent.method() === "POST" && url.pathname === "/api/agent/sessions") sessionPosts++;
    if (requestEvent.method() === "GET" && url.pathname === "/api/agent/sessions" && url.searchParams.get("scope_kind") === "creator" && url.searchParams.get("creator_id") === creator.id) creatorFilterReads++;
  });
  await page.route("**/api/agent/sessions/*/turns", route => route.fulfill({
    status: 503, json: { error: { message: "隔离验收：模型响应故障注入" } },
  }));

  await page.goto("/");
  await page.getByRole("button", { name: `查看账号 ${creator.display_name}` }).click();
  await page.getByRole("button", { name: "打开账号对话" }).click();
  const panel = page.locator(".account-chat-panel");
  await expect(panel).toBeVisible();
  await expect(panel.getByRole("heading", { name: creator.display_name, exact: true })).toBeVisible();
  await expect(panel.locator(".agent-welcome, .agent-heading")).toHaveCount(0);
  expect(sessionPosts).toBe(0);
  await page.screenshot({ path: info.outputPath("account-chat-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("account-chat-mobile.png"), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 900 });

  const input = panel.getByRole("textbox", { name: "给 Agent 的消息" });
  await input.fill("A 账号的草稿不能泄漏给 B。");
  await page.getByRole("button", { name: "关闭账号对话" }).click();
  await expect(panel).toBeHidden();
  await page.getByRole("button", { name: "打开账号对话" }).click();
  await expect(input).toHaveValue("A 账号的草稿不能泄漏给 B。");
  await page.getByRole("button", { name: `查看账号 ${otherCreator.display_name}` }).click();
  await expect(panel).toBeVisible();
  await expect(panel.getByRole("heading", { name: otherCreator.display_name, exact: true })).toBeVisible();
  await expect(panel.getByRole("textbox", { name: "给 Agent 的消息" })).toHaveValue("");
  await panel.getByRole("textbox", { name: "给 Agent 的消息" }).fill("B 账号自己的草稿。");
  await page.getByRole("button", { name: `查看账号 ${creator.display_name}` }).click();
  await expect(panel.getByRole("heading", { name: creator.display_name, exact: true })).toBeVisible();
  await expect(input).toHaveValue("A 账号的草稿不能泄漏给 B。");
  expect(sessionPosts).toBe(0);

  await page.reload();
  await expect(page.locator(".account-chat-panel")).toBeVisible();
  await expect(page.locator(".account-chat-panel").getByRole("textbox", { name: "给 Agent 的消息" })).toHaveValue("A 账号的草稿不能泄漏给 B。");
  expect(sessionPosts).toBe(0);
  expect(creatorFilterReads).toBeGreaterThan(0);

  await page.locator(".account-chat-panel").getByRole("textbox", { name: "给 Agent 的消息" }).fill("查看这个账号的栏目");
  await page.locator(".account-chat-panel").getByRole("button", { name: "发送 ↑" }).click();
  await expect(page.getByRole("alert")).toContainText("模型响应故障注入");
  expect(sessionPosts).toBe(1);

  const sessions = await request.get(`/api/agent/sessions?scope_kind=creator&creator_id=${encodeURIComponent(creator.id)}`);
  const result = await sessions.json() as { items: Array<{ id: string; creator_id: string | null; scope_kind: string }> };
  expect(result.items).toHaveLength(1);
  expect(result.items[0].creator_id).toBe(creator.id);
  expect(result.items[0].scope_kind).toBe("creator");
  expect(await page.evaluate((key) => sessionStorage.getItem(key), `creatoros.agent.selected-chat.v1:creator%3A${creator.id}`)).toBe(result.items[0].id);
  await page.reload();
  expect(new URL(page.url()).searchParams.has("chat")).toBe(false);
  const restoredPanel = page.locator(".account-chat-panel");
  await restoredPanel.getByRole("button", { name: "历史对话", exact: true }).click();
  await expect(restoredPanel.locator('.agent-history-list button[aria-current="page"]')).toBeVisible();
  await expect(restoredPanel.getByRole("heading", { name: creator.display_name, exact: true })).toBeVisible();
  expect(sessionPosts).toBe(1);
});

test("account conversation stays mounted on task links, back and reload without switching scope or resending", async ({ page, request }, info) => {
  const owner = await createCreator(request, "跨页面账号甲");
  const other = await createCreator(request, "链接目的账号乙");
  const response = await request.post(`/api/creators/${other.id}/series`, { data: { name: "另一账号的链接栏目" } });
  const series = await response.json();
  const saved = await createSession(request, owner.id);
  const path = `/series/${series.id}`;
  const doc = { ...saved, title: "跨页面持续对话", version: 2, status: "running", error: null, updated_at: "2026-10-17T01:00:00Z", has_older: false,
    entries: [{ kind: "assistant", text: "正在核对任务。", links: [{ url: path, label: "查看栏目", source_tool: "list_series_topics" }], complete: false }] };
  // Only the chat snapshot/SSE is controlled; destination pages and accounts
  // use the isolated real SQLite service. This is navigation/race QA, not Eval.
  await page.route(`**/api/agent/sessions/${saved.id}`, route => route.fulfill({ json: doc }));
  await page.addInitScript(({ creatorId, sessionId }) => {
    sessionStorage.setItem(`creatoros.agent.selected-chat.v1:creator%3A${creatorId}`, sessionId);
    class EventSourceProbe extends EventTarget {
      static CONNECTING = 0; static OPEN = 1; static CLOSED = 2;
      onopen: (() => void) | null = null; onerror: (() => void) | null = null;
      readyState = 1;
      constructor(public url: string) { super(); (window as any).__chatStreams = [...((window as any).__chatStreams ?? []), this]; }
      close() { this.readyState = 2; }
    }
    window.EventSource = EventSourceProbe as unknown as typeof EventSource;
  }, { creatorId: owner.id, sessionId: saved.id });
  let posts = 0;
  page.on("request", event => { if (event.method() === "POST") posts++; });
  await page.goto(`/?creator=${owner.id}`);
  await page.getByRole("button", { name: "打开账号对话" }).click();
  const panel = page.locator(".account-chat-panel");
  const draft = panel.getByRole("textbox", { name: "给 Agent 的消息" });
  await expect(panel.getByText("正在核对任务。", { exact: true })).toBeVisible();
  await draft.fill("保持这条草稿，不要发送。");
  await panel.locator(".agent-composer").evaluate(element => { element.setAttribute("data-persistence-probe", "same-node"); });
  await expect.poll(() => page.evaluate(() => (window as any).__chatStreams?.length ?? 0)).toBe(1);
  await panel.getByRole("link", { name: "查看栏目", exact: true }).click();
  await expect(page).toHaveURL(url => url.pathname === "/" && url.searchParams.get("series") === series.id);
  await expect(panel.getByRole("heading", { name: owner.display_name, exact: true })).toBeVisible();
  await expect(draft).toHaveValue("保持这条草稿，不要发送。");
  await expect(panel.locator('[data-persistence-probe="same-node"]')).toBeVisible();
  expect(await page.evaluate(() => (window as any).__chatStreams.length)).toBe(1);
  expect(await page.evaluate(() => (window as any).__chatStreams[0].readyState)).toBe(1);
  await page.getByRole("link", { name: "Skill", exact: true }).click();
  await expect(page).toHaveURL(/\/skills$/);
  await expect(panel).toBeVisible();
  await expect(draft).toHaveValue("保持这条草稿，不要发送。");
  await page.screenshot({ path: info.outputPath("persistent-chat-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("persistent-chat-mobile.png"), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goBack();
  await expect(panel.locator('[data-persistence-probe="same-node"]')).toBeVisible();
  await page.reload();
  await expect(panel).toBeVisible();
  await expect(panel.getByRole("heading", { name: owner.display_name, exact: true })).toBeVisible();
  await expect(draft).toHaveValue("保持这条草稿，不要发送。");
  expect(posts).toBe(0);
  // An already-mounted hidden drawer must accept an explicit different
  // conversation from /agent, rather than its stale in-memory selection.
  const nextSession = await createSession(request, owner.id);
  const nextDoc = await (await request.get(`/api/agent/sessions/${nextSession.id}`)).json() as { title: string };
  await page.goto(`/agent?creator=${owner.id}&chat=${nextSession.id}`);
  await expect(panel).toBeHidden();
  await page.getByRole("link", { name: "返回账号工作台" }).click();
  await expect(panel).toBeVisible();
  await expect(panel.getByRole("heading", { name: owner.display_name, exact: true })).toBeVisible();
  await panel.getByRole("button", { name: "历史对话", exact: true }).click();
  await expect(panel.locator('.agent-history-list button[aria-current="page"]')).toContainText(nextDoc.title);
  await panel.getByRole("button", { name: "历史对话", exact: true }).click();
  await expect(panel.locator(".agent-transcript")).not.toContainText("正在核对任务。");
  expect(await page.evaluate(key => sessionStorage.getItem(key), `creatoros.agent.selected-chat.v1:creator%3A${owner.id}`)).toBe(nextSession.id);
  await panel.getByRole("button", { name: "关闭账号对话" }).click();
  await page.getByRole("button", { name: `查看账号 ${other.display_name}` }).click();
  await page.getByRole("button", { name: "打开账号对话" }).click();
  await expect(panel.getByRole("heading", { name: other.display_name, exact: true })).toBeVisible();
  await expect(panel.getByRole("alert")).toHaveCount(0);
  expect(await page.evaluate(key => sessionStorage.getItem(key), `creatoros.agent.selected-chat.v1:creator%3A${other.id}`)).toBeNull();
  expect(posts).toBe(0);
});

test("deep links derive a bound session scope; mismatched scopes cannot send", async ({ page, request }) => {
  const creator = await createCreator(request, "绑定账号甲");
  const otherCreator = await createCreator(request, "绑定账号乙");
  const seriesResponse = await request.post(`/api/creators/${otherCreator.id}/series`, { data: { name: "乙账号栏目" } });
  expect(seriesResponse.status()).toBe(201);
  const accountSession = await createSession(request, creator.id);
  const overviewSession = await createSession(request, null);
  let turnPosts = 0;
  const listScopes: string[] = [];
  page.on("request", requestEvent => {
    if (requestEvent.method() === "POST" && new URL(requestEvent.url()).pathname.endsWith("/turns")) turnPosts++;
    const url = new URL(requestEvent.url());
    if (requestEvent.method() === "GET" && url.pathname === "/api/agent/sessions") listScopes.push(url.searchParams.get("scope_kind") ?? "unfiltered");
  });

  await page.goto(`/?creator=${creator.id}`);
  await page.getByRole("button", { name: `查看账号 ${otherCreator.display_name}` }).click();
  await expect.poll(() => new URL(page.url()).searchParams.get("creator")).toBe(otherCreator.id);
  await expect(page.getByRole("heading", { name: otherCreator.display_name, level: 1 })).toBeVisible();

  await page.goto("/agent");
  await expect(page.locator(".agent-history-list button")).toHaveCount(1);
  expect(listScopes).toContain("overview");
  await page.goto(`/agent?chat=${accountSession.id}`);
  await expect(page).toHaveURL(new RegExp(`chat=${accountSession.id}.*creator=${creator.id}`));
  await expect(page.locator(".agent-history-list button")).toHaveCount(1);
  expect(listScopes).toContain("creator");
  await expect(page.getByRole("link", { name: "返回账号工作台" })).toHaveAttribute("href", `/?creator=${creator.id}`);

  await page.goto(`/agent?chat=${overviewSession.id}&creator=${otherCreator.id}`);
  await expect(page.getByRole("alert")).toContainText("属于其他账号范围");
  const input = page.getByRole("textbox", { name: "给 Agent 的消息" });
  await input.fill("请查看账号");
  await expect(page.getByRole("button", { name: "发送 ↑" })).toBeDisabled();
  expect(turnPosts).toBe(0);

  await page.locator(".agent-new").click();
  await expect(page).toHaveURL(new RegExp(`/agent\\?creator=${otherCreator.id}$`));
  expect(turnPosts).toBe(0);

  const missingCreatorResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/creators/missing-e2e-account");
  await page.goto("/agent?creator=missing-e2e-account");
  expect((await missingCreatorResponse).status()).toBe(404);
  await expect(page.getByRole("alert")).toContainText("无法读取账号");
  await page.getByRole("textbox", { name: "给 Agent 的消息" }).fill("不能发送");
  await expect(page.getByRole("button", { name: "发送 ↑" })).toBeDisabled();
  expect(turnPosts).toBe(0);
});
