import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { configureImageDefault } from "./fixtures/image-default";

async function createCreator(request: APIRequestContext, name: string) {
  const response = await request.post("/api/creators", { data: { display_name: name } });
  expect(response.status()).toBe(201);
  return response.json() as Promise<{ id: string; display_name: string }>;
}

async function switchAccount(page: Page, name: string) {
  await page.getByRole("button", { name: "切换账号", exact: true }).click();
  await page.getByRole("dialog", { name: "切换账号", exact: true })
    .getByRole("button", { name: `切换到账号 ${name}`, exact: true }).click();
}

async function createSession(request: APIRequestContext, creatorId: string | null) {
  const response = await request.post("/api/agent/sessions", { data: { creator_id: creatorId } });
  expect(response.status()).toBe(201);
  return response.json() as Promise<{ id: string; creator_id: string | null; scope_kind: "overview" | "creator" }>;
}

test("a page chunk failure keeps the account chat and lets the user reload", async ({ page, request }) => {
  const creator = await createCreator(request, "页面加载失败时的草稿");
  await page.goto(`/?creator=${creator.id}`);
  await page.getByRole("button", { name: "打开账号对话" }).click();
  const panel = page.locator(".account-chat-panel");
  const draft = panel.getByRole("textbox", { name: "给 Agent 的消息" });
  await draft.fill("页面失败后请保留这段草稿");
  await page.route(/\/assets\/SkillsPage-[^/]+\.js$/, route => route.abort("failed"));
  await page.getByRole("link", { name: "Skill", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "页面暂时加载失败" })).toBeVisible();
  await expect(panel).toBeVisible();
  await expect(draft).toHaveValue("页面失败后请保留这段草稿");
  await page.unrouteAll({ behavior: "wait" });
  await page.getByRole("button", { name: "重新加载页面", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Skill 库", exact: true })).toBeVisible();
  await expect(page.locator(".account-chat-panel")).toBeVisible();
  await expect(page.locator(".account-chat-panel").getByRole("textbox", { name: "给 Agent 的消息" }))
    .toHaveValue("页面失败后请保留这段草稿");
});

test("the account launcher waits for the workspace owner instead of opening overview during loading", async ({ page, request }) => {
  const account = await createCreator(request, "延迟载入账号");
  let release!: () => void;
  const ready = new Promise<void>(resolve => { release = resolve; });
  let requested = false;
  await page.route(url => url.pathname === "/api/creator-directory", async route => { requested = true; await ready; await route.continue(); });
  try {
    await page.goto(`/?creator=${account.id}`);
    const launcher = page.getByRole("button", { name: "打开账号对话" });
    await expect.poll(() => requested).toBe(true);
    await expect(launcher).toBeDisabled();
    release();
    await expect(launcher).toBeEnabled();
    await launcher.click();
    await expect(page.locator(".account-chat-panel").getByRole("heading", { name: account.display_name, exact: true })).toBeVisible();
  } finally { release(); }
});

test("workspace waits for the requested account instead of reusing the previous account", async ({ page, request }) => {
  const a = await createCreator(request, "旧账号甲");
  const b = await createCreator(request, "目标账号乙");
  await page.goto(`/workspace?creator=${a.id}`);
  await expect(page.getByRole("heading", { name: a.display_name, level: 1 })).toBeVisible();
  const launcher = page.getByRole("button", { name: "打开账号对话" });
  await launcher.click();
  const panel = page.locator(".account-chat-panel");
  await expect(panel.getByRole("heading", { name: a.display_name, exact: true })).toBeVisible();
  await page.getByRole("button", { name: "关闭账号对话" }).click();
  let release!: () => void;
  const ready = new Promise<void>(resolve => { release = resolve; });
  let requested = false;
  await page.route(url => url.pathname === "/api/creator-directory", async route => { requested = true; await ready; await route.continue(); });
  await page.route("**/api/agent/sessions/*/turns", route => route.fulfill({
    status: 503, json: { error: { message: "隔离测试不调用模型" } },
  }));
  try {
    await page.goto(`/workspace?creator=${b.id}`);
    await expect.poll(() => requested).toBe(true);
    await expect(launcher).toBeDisabled();
    release();
    await expect(launcher).toBeEnabled();
    await launcher.click();
    await expect(panel.getByRole("heading", { name: b.display_name, exact: true })).toBeVisible();
    const sessionPost = page.waitForResponse(response => response.request().method() === "POST"
      && new URL(response.url()).pathname === "/api/agent/sessions");
    await panel.getByRole("textbox", { name: "给 Agent 的消息" }).fill("测试当前账号");
    await panel.getByRole("button", { name: "发送 ↑" }).click();
    const created = await sessionPost;
    expect(created.request().postDataJSON().creator_id).toBe(b.id);
    expect(created.status()).toBe(201);
    const session = await created.json() as { id: string; creator_id: string; scope_kind: string };
    expect(session.creator_id).toBe(b.id);
    expect(session.scope_kind).toBe("creator");
    const saved = await request.get(`/api/agent/sessions/${session.id}`).then(response => response.json());
    expect(saved.creator_id).toBe(b.id);
    expect(saved.scope_kind).toBe("creator");
  } finally { release(); }
});

test("workspace launcher uses overview for no accounts and refuses a missing selected account", async ({ page, request }) => {
  const a = await createCreator(request, "无效账号前的旧账号");
  await page.goto(`/workspace?creator=${a.id}`);
  await expect(page.getByRole("heading", { name: a.display_name, level: 1 })).toBeVisible();
  const launcher = page.getByRole("button", { name: "打开账号对话" });
  await launcher.click();
  await page.getByRole("button", { name: "关闭账号对话" }).click();
  await page.goto("/workspace?creator=missing-account");
  await expect(page.getByText("账号不存在，请从账号切换中重新选择。", { exact: true })).toBeVisible();
  await expect(launcher).toBeDisabled();
  // Represent an empty database at the account-list HTTP boundary; no write or
  // cross-account fallback is involved in this controlled empty-state check.
  await page.route(url => url.pathname === "/api/creator-directory", route => route.fulfill({
    json: { items: [] },
  }));
  await page.goto("/workspace");
  await expect(launcher).toBeEnabled();
  await launcher.click();
  await expect(page.locator(".account-chat-panel").getByRole("heading", { name: "全部账号", exact: true })).toBeVisible();
});

test("a cached account deleted in another session becomes unavailable without closing an open conversation", async ({ page, request }) => {
  const account = await createCreator(request, `被其他会话删除-${crypto.randomUUID().slice(0, 6)}`);
  await page.clock.install();
  await page.goto(`/workspace?creator=${account.id}`);
  await expect(page.getByRole("heading", { name: account.display_name, level: 1, exact: true })).toBeVisible();
  const launcher = page.getByRole("button", { name: "打开账号对话", exact: true });
  await launcher.click();
  const panel = page.locator(".account-chat-panel");
  await expect(panel.getByRole("heading", { name: account.display_name, exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Skill", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Skill 库", exact: true })).toBeVisible();
  const deleted = await request.post(`/api/creators/${account.id}/delete`, { data: {} });
  expect(deleted.ok(), await deleted.text()).toBeTruthy();
  await page.clock.fastForward(6000);
  const missing = page.waitForResponse(response => new URL(response.url()).pathname === `/api/creators/${account.id}`
    && response.status() === 404);
  await page.goBack();
  await missing;
  await expect(page.getByText("账号不存在，请从账号切换中重新选择。", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: account.display_name, level: 1, exact: true })).toHaveCount(0);
  await expect(panel.getByRole("heading", { name: account.display_name, exact: true })).toBeVisible();
  await panel.getByRole("button", { name: "关闭账号对话", exact: true }).click();
  await expect(launcher).toBeDisabled();
});

test("a failed refresh of cached account details reports the error and disables reopening until retry", async ({ page, request }) => {
  const account = await createCreator(request, `账号刷新故障-${crypto.randomUUID().slice(0, 6)}`);
  await page.clock.install();
  await page.goto(`/workspace?creator=${account.id}`);
  await expect(page.getByRole("heading", { name: account.display_name, level: 1, exact: true })).toBeVisible();
  const launcher = page.getByRole("button", { name: "打开账号对话", exact: true });
  await expect(launcher).toBeEnabled();
  await page.getByRole("link", { name: "Skill", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Skill 库", exact: true })).toBeVisible();
  let attempts = 0;
  await page.route(`**/api/creators/${account.id}`, route => {
    attempts++;
    return attempts === 1 ? route.fulfill({ status: 503, json: { detail: "受控账号详情故障" } }) : route.continue();
  });
  await page.clock.fastForward(6000);
  await page.goBack();
  await expect(page.getByRole("alert")).toContainText("读取当前账号失败");
  await expect(page.getByText("账号不存在，请从账号切换中重新选择。", { exact: true })).toHaveCount(0);
  await expect(launcher).toBeDisabled();
  await page.getByRole("button", { name: "重新连接", exact: true }).click();
  await expect(page.getByRole("heading", { name: account.display_name, level: 1, exact: true })).toBeVisible();
  await expect(launcher).toBeEnabled();
  await expect(launcher).toHaveAttribute("title", `与 ${account.display_name} 对话`);
});

test("the default account is verified before enabling its conversation", async ({ page, request }) => {
  await createCreator(request, `默认账号验证-${crypto.randomUUID().slice(0, 6)}`);
  const directory = await request.get("/api/creator-directory").then(response => response.json());
  const account = directory.items[0] as { id: string; display_name: string };
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let requested = false;
  await page.route(`**/api/creators/${account.id}`, async route => {
    requested = true;
    await gate;
    try { await route.continue(); } catch { /* navigation can cancel the request */ }
  });
  try {
    await page.goto("/workspace");
    await expect.poll(() => requested).toBe(true);
    const launcher = page.getByRole("button", { name: "打开账号对话", exact: true });
    await expect(launcher).toBeDisabled();
    release();
    await expect(page.getByRole("heading", { name: account.display_name, level: 1, exact: true })).toBeVisible();
    await expect(launcher).toHaveAttribute("title", `与 ${account.display_name} 对话`);
    await expect(launcher).toBeEnabled();
  } finally { release(); }
});

test("a default account deleted before its real detail read does not become a valid account", async ({ page, request }) => {
  const account = await createCreator(request, `默认账号已删除-${crypto.randomUUID().slice(0, 6)}`);
  const other = await createCreator(request, `待选择的账号-${crypto.randomUUID().slice(0, 6)}`);
  // Select the newly created real account as the default, independently of
  // other accounts left by this shared isolated browser suite.
  await page.route("**/api/creator-directory", async route => {
    const response = await route.fetch();
    const body = await response.json();
    body.items = [account.id, other.id].map(id => body.items.find((item: { id: string }) => item.id === id)).filter(Boolean);
    await route.fulfill({ response, json: body });
  });
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let requested = false;
  await page.route(`**/api/creators/${account.id}`, async route => {
    requested = true;
    await gate;
    try { await route.continue(); } catch { /* navigation can cancel the request */ }
  });
  try {
    await page.goto("/workspace");
    await expect.poll(() => requested).toBe(true);
    const deleted = await request.post(`/api/creators/${account.id}/delete`, { data: {} });
    expect(deleted.ok(), await deleted.text()).toBeTruthy();
    release();
    await expect(page.getByText("账号不存在，请从账号切换中重新选择。", { exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: account.display_name, level: 1, exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "打开账号对话", exact: true })).toBeDisabled();
    // An unavailable account has no create control in the current workspace.
    await expect(page.getByRole("button", { name: "新建栏目", exact: true })).toHaveCount(0);
    await switchAccount(page, other.display_name);
    await expect(page.getByRole("heading", { name: other.display_name, level: 1, exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "打开账号对话", exact: true }))
      .toHaveAttribute("title", `与 ${other.display_name} 对话`);
  } finally { release(); }
});

test("a late default-account refresh cannot replace the account chosen afterward", async ({ page, request }) => {
  const other = await createCreator(request, `切换目标-${crypto.randomUUID().slice(0, 6)}`);
  const directory = await request.get("/api/creator-directory").then(response => response.json());
  const initial = directory.items.find((item: { id: string }) => item.id !== other.id) as { id: string; display_name: string } | undefined;
  if (!initial) {
    await createCreator(request, `默认账号-${crypto.randomUUID().slice(0, 6)}`);
  }
  const refreshed = await request.get("/api/creator-directory").then(response => response.json());
  const account = refreshed.items[0] as { id: string; display_name: string };
  const destination = account.id === other.id ? refreshed.items.find((item: { id: string }) => item.id !== account.id) : other;
  expect(destination).toBeTruthy();
  await page.clock.install();
  await page.goto("/workspace");
  await expect(page.getByRole("heading", { name: account.display_name, level: 1, exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Skill", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Skill 库", exact: true })).toBeVisible();
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let captured = false;
  await page.route(`**/api/creators/${account.id}`, async route => {
    const response = await route.fetch();
    captured = true;
    await gate;
    try { await route.fulfill({ response }); } catch { /* The old query can be cancelled on switch. */ }
  });
  try {
    await page.clock.fastForward(6000);
    await page.goBack();
    await expect.poll(() => captured).toBe(true);
    await switchAccount(page, destination.display_name);
    await expect(page.getByRole("heading", { name: destination.display_name, level: 1, exact: true })).toBeVisible();
    const launcher = page.getByRole("button", { name: "打开账号对话", exact: true });
    await expect(launcher).toHaveAttribute("title", `与 ${destination.display_name} 对话`);
    release();
    await expect(page.getByRole("heading", { name: destination.display_name, level: 1, exact: true })).toBeVisible();
    await expect(launcher).toHaveAttribute("title", `与 ${destination.display_name} 对话`);
  } finally { release(); }
});

test("an open account Skill keeps its scope when the account is deleted elsewhere", async ({ page, request }) => {
  const account = await createCreator(request, `Skill 归属失效-${crypto.randomUUID().slice(0, 6)}`);
  const fixtureResponse = await request.post("/test/skill-files", { data: {} });
  expect(fixtureResponse.ok(), await fixtureResponse.text()).toBeTruthy();
  const fixture = await fixtureResponse.json();
  const binding = await request.post(`/api/creators/${account.id}/skills`, {
    data: { skill_id: fixture.mind.id, request_id: crypto.randomUUID() },
  });
  expect(binding.ok(), await binding.text()).toBeTruthy();
  await page.clock.install();
  await page.goto(`/workspace?creator=${account.id}&view=skills`);
  const library = page.getByRole("region", { name: "账号 Skill 库", exact: true });
  await library.getByRole("button", { name: /^inspector-mind/ }).click();
  const dialog = page.getByRole("dialog", { name: "inspector-mind", exact: true });
  await expect(dialog.getByText("完整 Skill 结尾标记", { exact: true })).toBeVisible();
  const sharedBefore = await request.get(`/api/producer-skills/${fixture.mind.id}/files/content?path=SKILL.md`).then(response => response.json());
  const createdCandidate = page.waitForResponse(response => response.request().method() === "POST"
    && new URL(response.url()).pathname === `/api/producer-skills/${fixture.mind.id}/versions`);
  await dialog.getByRole("button", { name: "创建修改候选", exact: true }).click();
  const createdResponse = await createdCandidate;
  expect(createdResponse.ok(), await createdResponse.text()).toBeTruthy();
  const candidate = await createdResponse.json() as { id: string };
  await expect(dialog.getByRole("combobox", { name: "查看 Skill 版本" })).toHaveValue(candidate.id);
  await dialog.getByRole("button", { name: "编辑", exact: true }).click();
  const editor = dialog.getByRole("textbox", { name: "编辑 SKILL.md", exact: true });
  const draft = `${await editor.inputValue()}\n\n账号失效前的未保存草稿。`;
  await editor.fill(draft);
  const candidateBefore = await request.get(`/api/producer-skills/${candidate.id}/files/content?path=SKILL.md`).then(response => response.json());
  const original = await dialog.elementHandle();
  expect(original).not.toBeNull();
  const unscoped: string[] = [];
  const writes: string[] = [];
  page.on("request", event => {
    const url = new URL(event.url());
    if (url.pathname.startsWith("/api/producer-skills/") && event.method() !== "GET") writes.push(event.method());
    if (url.pathname.startsWith("/api/producer-skills/")
        && !url.searchParams.has("creator_id")) unscoped.push(url.pathname);
  });
  const deleted = await request.post(`/api/creators/${account.id}/delete`, { data: {} });
  expect(deleted.ok(), await deleted.text()).toBeTruthy();
  await page.clock.fastForward(6000);
  const missing = page.waitForResponse(response => new URL(response.url()).pathname === `/api/creators/${account.id}`
    && response.status() === 404);
  await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
  await missing;
  await expect(page.getByText("账号不存在，请从账号切换中重新选择。", { exact: true })).toBeVisible();
  await expect.poll(() => original!.evaluate(element => element.isConnected)).toBe(true);
  await expect(dialog.getByRole("alert").filter({ hasText: "当前账号已切换或暂时无法确认" })).toBeVisible();
  await expect(editor).toHaveValue(draft);
  const save = dialog.getByRole("button", { name: "保存文件", exact: true });
  await expect(save).toBeDisabled();
  await save.evaluate((button: HTMLButtonElement) => button.click());
  expect(unscoped).toEqual([]);
  expect(writes).toEqual([]);
  const sharedAfter = await request.get(`/api/producer-skills/${fixture.mind.id}/files/content?path=SKILL.md`).then(response => response.json());
  const candidateAfter = await request.get(`/api/producer-skills/${candidate.id}/files/content?path=SKILL.md`).then(response => response.json());
  expect(sharedAfter).toMatchObject({ content: sharedBefore.content, digest: sharedBefore.digest });
  expect(candidateAfter).toMatchObject({ content: candidateBefore.content, digest: candidateBefore.digest });
});

test("a temporary account read failure preserves the Skill draft until verified recovery", async ({ page, request }) => {
  const account = await createCreator(request, `Skill 临时离线-${crypto.randomUUID().slice(0, 6)}`);
  const fixture = await request.post("/test/skill-files", { data: {} }).then(response => response.json());
  const binding = await request.post(`/api/creators/${account.id}/skills`, {
    data: { skill_id: fixture.mind.id, request_id: crypto.randomUUID() },
  });
  expect(binding.ok(), await binding.text()).toBeTruthy();
  await page.clock.install();
  await page.goto(`/workspace?creator=${account.id}&view=skills`);
  await page.getByRole("region", { name: "账号 Skill 库", exact: true }).getByRole("button", { name: /^inspector-mind/ }).click();
  const dialog = page.getByRole("dialog", { name: "inspector-mind", exact: true });
  const sharedBefore = await request.get(`/api/producer-skills/${fixture.mind.id}/files/content?path=SKILL.md`).then(response => response.json());
  const created = page.waitForResponse(response => response.request().method() === "POST"
    && new URL(response.url()).pathname === `/api/producer-skills/${fixture.mind.id}/versions`);
  await dialog.getByRole("button", { name: "创建修改候选", exact: true }).click();
  const response = await created;
  expect(response.ok(), await response.text()).toBeTruthy();
  const candidate = await response.json() as { id: string };
  await expect(dialog.getByRole("combobox", { name: "查看 Skill 版本" })).toHaveValue(candidate.id);
  await dialog.getByRole("button", { name: "编辑", exact: true }).click();
  const editor = dialog.getByRole("textbox", { name: "编辑 SKILL.md", exact: true });
  const draft = `${await editor.inputValue()}\n\n账号恢复前保留的草稿。`;
  await editor.fill(draft);
  let attempts = 0;
  await page.route(`**/api/creators/${account.id}`, route => {
    attempts++;
    return attempts === 1 ? route.fulfill({ status: 503, json: { detail: "受控的账号详情故障" } }) : route.continue();
  });
  await page.clock.fastForward(6000);
  const failure = page.waitForResponse(result => new URL(result.url()).pathname === `/api/creators/${account.id}`
    && result.status() === 503);
  await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
  await failure;
  const warning = dialog.getByRole("alert").filter({ hasText: "当前账号已切换或暂时无法确认" });
  await expect(warning).toBeVisible();
  await expect(editor).toHaveValue(draft);
  const save = dialog.getByRole("button", { name: "保存文件", exact: true });
  await expect(save).toBeDisabled();
  await warning.getByRole("button", { name: "重新核验账号", exact: true }).click();
  await expect(warning).toHaveCount(0);
  await expect(editor).toHaveValue(draft);
  await expect(save).toBeEnabled();
  const saved = page.waitForResponse(result => result.request().method() === "PUT"
    && new URL(result.url()).pathname === `/api/producer-skills/${candidate.id}/files/content`);
  await save.click();
  const saveResponse = await saved;
  expect(saveResponse.ok(), await saveResponse.text()).toBeTruthy();
  expect(new URL(saveResponse.url()).searchParams.get("creator_id")).toBe(account.id);
  const candidateAfter = await request.get(`/api/producer-skills/${candidate.id}/files/content?path=SKILL.md`).then(result => result.json());
  const sharedAfter = await request.get(`/api/producer-skills/${fixture.mind.id}/files/content?path=SKILL.md`).then(result => result.json());
  expect(candidateAfter.content).toBe(draft);
  expect(sharedAfter).toMatchObject({ content: sharedBefore.content, digest: sharedBefore.digest });
});

test("switching accounts keeps an open Skill draft bound to its original account", async ({ page, request }) => {
  const other = await createCreator(request, `Skill 切换乙-${crypto.randomUUID().slice(0, 6)}`);
  const account = await createCreator(request, `Skill 切换甲-${crypto.randomUUID().slice(0, 6)}`);
  const fixture = await request.post("/test/skill-files", { data: {} }).then(response => response.json());
  const binding = await request.post(`/api/creators/${account.id}/skills`, {
    data: { skill_id: fixture.mind.id, request_id: crypto.randomUUID() },
  });
  expect(binding.ok(), await binding.text()).toBeTruthy();
  await page.goto(`/workspace?creator=${other.id}`);
  await switchAccount(page, account.display_name);
  await page.getByRole("navigation", { name: "账号工作区" }).getByRole("button", { name: "可用 Skill", exact: true }).click();
  await page.getByRole("region", { name: "账号 Skill 库", exact: true }).getByRole("button", { name: /^inspector-mind/ }).click();
  const dialog = page.getByRole("dialog", { name: "inspector-mind", exact: true });
  const created = page.waitForResponse(response => response.request().method() === "POST"
    && new URL(response.url()).pathname === `/api/producer-skills/${fixture.mind.id}/versions`);
  await dialog.getByRole("button", { name: "创建修改候选", exact: true }).click();
  const response = await created;
  expect(response.ok(), await response.text()).toBeTruthy();
  const candidate = await response.json() as { id: string };
  await expect(dialog.getByRole("combobox", { name: "查看 Skill 版本" })).toHaveValue(candidate.id);
  await dialog.getByRole("button", { name: "编辑", exact: true }).click();
  const editor = dialog.getByRole("textbox", { name: "编辑 SKILL.md", exact: true });
  const draft = `${await editor.inputValue()}\n\n账号切换时保留的草稿。`;
  await editor.fill(draft);
  const unscoped: string[] = [], writes: string[] = [];
  page.on("request", event => {
    const url = new URL(event.url());
    if (!url.pathname.startsWith("/api/producer-skills/")) return;
    if (event.method() !== "GET") writes.push(event.method());
    if (!url.searchParams.has("creator_id")) unscoped.push(url.pathname);
  });
  await page.goBack(); // leave the Skill tab, keeping account A
  await page.goBack(); // return to account B through the router
  await expect(page).toHaveURL(url => url.searchParams.get("creator") === other.id);
  await expect(dialog.getByRole("alert").filter({ hasText: "当前账号已切换或暂时无法确认" })).toBeVisible();
  await expect(editor).toHaveValue(draft);
  await expect(dialog.getByRole("button", { name: "保存文件", exact: true })).toBeDisabled();
  expect(unscoped).toEqual([]);
  expect(writes).toEqual([]);
  await page.goForward();
  await expect(page).toHaveURL(url => url.searchParams.get("creator") === account.id);
  await expect(dialog.getByRole("alert").filter({ hasText: "当前账号已切换或暂时无法确认" })).toHaveCount(0);
  await expect(editor).toHaveValue(draft);
  await expect(dialog.getByRole("button", { name: "保存文件", exact: true })).toBeEnabled();
});

test("a malformed account directory is not mistaken for an empty workspace", async ({ page, request }) => {
  await createCreator(request, "目录响应损坏的账号");
  const directory = await request.get("/api/creator-directory").then(response => response.json());
  const account = directory.items[0] as { id: string; display_name: string };
  let attempts = 0;
  await page.route("**/api/creator-directory", route => {
    attempts++;
    return attempts === 1 ? route.fulfill({ status: 200, json: {} }) : route.continue();
  });
  await page.goto("/workspace");
  await expect(page.getByRole("alert")).toContainText("账号目录返回格式无效");
  await expect(page.getByRole("button", { name: "打开账号对话" })).toBeDisabled();
  await page.getByRole("button", { name: "重新连接", exact: true }).click();
  await expect(page.getByRole("heading", { name: account.display_name, level: 1, exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "打开账号对话" })).toHaveAttribute("title", `与 ${account.display_name} 对话`);
});

test("an explicit different account cannot reuse an old workspace readiness marker", async ({ page, request }) => {
  const a = await createCreator(request, "返回目标账号甲");
  const b = await createCreator(request, "作品所属账号乙");
  const seriesResponse = await request.post(`/api/creators/${b.id}/series`, { data: { name: "乙的作品栏目" } });
  expect(seriesResponse.ok(), await seriesResponse.text()).toBeTruthy();
  const series = await seriesResponse.json() as { id: string };
  await configureImageDefault(request, b.id, series.id);
  const queuedResponse = await request.post(`/api/series/${series.id}/queue`, {
    data: { request_id: crypto.randomUUID(), topics: [{ title: "乙账号作品" }] },
  });
  expect(queuedResponse.ok(), await queuedResponse.text()).toBeTruthy();
  const queued = await queuedResponse.json() as { topic_ids: string[] };
  const runResponse = await request.post("/api/runs", { data: { topic_id: queued.topic_ids[0] } });
  expect(runResponse.ok(), await runResponse.text()).toBeTruthy();
  const run = await runResponse.json() as { id: string };
  const saved = await createSession(request, a.id);
  const doc = { ...saved, title: "跨账号作品链接", version: 2, status: "completed", error: null,
    updated_at: "2026-10-17T01:00:00Z", has_older: false,
    entries: [{ kind: "assistant", text: "查看乙的作品。", links: [{ url: `/runs/${run.id}`,
      label: "查看乙的作品", source_tool: "list_series_topics" }], complete: true }] };
  await page.route(`**/api/agent/sessions/${saved.id}`, route => route.fulfill({ json: doc }));
  await page.addInitScript(({ creatorId, sessionId }) => {
    sessionStorage.setItem(`creatoros.agent.selected-chat.v1:creator%3A${creatorId}`, sessionId);
  }, { creatorId: a.id, sessionId: saved.id });
  await page.clock.install();
  await page.goto(`/workspace?creator=${a.id}`);
  const launcher = page.getByRole("button", { name: "打开账号对话" });
  await launcher.click();
  const panel = page.locator(".account-chat-panel");
  await panel.getByRole("link", { name: "查看乙的作品", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/runs/${run.id}$`));
  await expect(panel.getByRole("heading", { name: a.display_name, exact: true })).toBeVisible();
  await panel.getByRole("button", { name: "关闭账号对话" }).click();
  await page.getByRole("button", { name: "讨论", exact: true }).click();
  await expect(panel.getByRole("heading", { name: b.display_name, exact: true })).toBeVisible();
  await panel.getByRole("button", { name: "关闭账号对话" }).click();
  // The workspace query is no longer observed; advance past React Query's
  // five-minute cache lifetime to exercise a real delayed account refresh.
  await page.clock.fastForward(301_000);
  let release!: () => void;
  const ready = new Promise<void>(resolve => { release = resolve; });
  let requested = false;
  await page.route(url => url.pathname === "/api/creator-directory", async route => { requested = true; await ready; await route.continue(); });
  try {
    await page.goBack();
    await expect.poll(() => requested).toBe(true);
    await expect(launcher).toBeDisabled();
    release();
    await expect(launcher).toBeEnabled();
    await launcher.click();
    await expect(panel.getByRole("heading", { name: a.display_name, exact: true })).toBeVisible();
  } finally { release(); }
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

  await page.goto(`/?creator=${creator.id}`);
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
  await switchAccount(page, otherCreator.display_name);
  await expect(panel).toBeVisible();
  await expect(panel.getByRole("heading", { name: otherCreator.display_name, exact: true })).toBeVisible();
  await expect(panel.getByRole("textbox", { name: "给 Agent 的消息" })).toHaveValue("");
  await panel.getByRole("textbox", { name: "给 Agent 的消息" }).fill("B 账号自己的草稿。");
  await switchAccount(page, creator.display_name);
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
  await switchAccount(page, other.display_name);
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
  await switchAccount(page, otherCreator.display_name);
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
