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

test("account entry stays lazy and the first turn is bound to that account", async ({ page, request }, info) => {
  const creator = await createCreator(request, "账号聊天隔离验收");
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
  await page.getByRole("link", { name: `与 ${creator.display_name} 对话` }).click();
  await expect(page).toHaveURL(new RegExp(`/agent\\?creator=${creator.id}`));
  await expect(page.getByRole("heading", { name: `从「${creator.display_name}」开始` })).toBeVisible();
  expect(sessionPosts).toBe(0);
  await page.screenshot({ path: info.outputPath("account-chat-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("account-chat-mobile.png"), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.reload();
  await expect(page.getByRole("heading", { name: `从「${creator.display_name}」开始` })).toBeVisible();
  expect(sessionPosts).toBe(0);
  expect(creatorFilterReads).toBeGreaterThan(0);

  await page.getByRole("textbox", { name: "给 Agent 的消息" }).fill("查看这个账号的栏目");
  await page.getByRole("button", { name: "发送 ↑" }).click();
  await expect(page.getByRole("alert")).toContainText("模型响应故障注入");
  expect(sessionPosts).toBe(1);

  const sessions = await request.get(`/api/agent/sessions?scope_kind=creator&creator_id=${encodeURIComponent(creator.id)}`);
  const result = await sessions.json() as { items: Array<{ id: string; creator_id: string | null; scope_kind: string }> };
  expect(result.items).toHaveLength(1);
  expect(result.items[0].creator_id).toBe(creator.id);
  expect(result.items[0].scope_kind).toBe("creator");
  await page.reload();
  await expect.poll(() => new URL(page.url()).searchParams.get("chat")).toBe(result.items[0].id);
  await expect.poll(() => new URL(page.url()).searchParams.get("creator")).toBe(creator.id);
  expect(sessionPosts).toBe(1);
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
  await page.locator(".rail-series").filter({ hasText: "乙账号栏目" }).click();
  await expect.poll(() => new URL(page.url()).searchParams.get("creator")).toBe(otherCreator.id);
  await expect(page.getByRole("heading", { name: "乙账号栏目" })).toBeVisible();

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
