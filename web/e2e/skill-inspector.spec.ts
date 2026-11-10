import { expect, test, type Page } from "@playwright/test";
import { limitVisibleSkillCatalog } from "./fixtures/skill-catalog";

async function openMind(page: Page) {
  await page.goto("/skills");
  await page.getByRole("searchbox", { name: "搜索 Skill" }).fill("inspector-mind");
  const card = page.getByRole("button", { name: "inspector-mind", exact: true });
  await card.click();
  const dialog = page.getByRole("dialog", { name: "inspector-mind" });
  await expect(dialog.getByText("完整 Skill 结尾标记", { exact: true })).toBeAttached();
  return dialog;
}

async function createCandidate(page: Page, dialog: ReturnType<Page["getByRole"]>) {
  await expect(dialog.getByRole("button", { name: "编辑", exact: true })).toHaveCount(0);
  const response = page.waitForResponse(item => item.request().method() === "POST" && /\/producer-skills\/[^/]+\/versions$/.test(new URL(item.url()).pathname));
  await dialog.getByRole("button", { name: "创建修改候选", exact: true }).click();
  const result = await response;
  expect(result.ok(), await result.text()).toBeTruthy();
  const candidate = await result.json() as { id: string };
  const selectionStarted = Date.now();
  const selector = dialog.getByRole("combobox", { name: "查看 Skill 版本" });
  await expect(selector).toHaveValue(candidate.id, { timeout: 20_000 });
  console.log(`Skill 候选可操作等待 ${Date.now() - selectionStarted}ms，版本数 ${await selector.locator("option").count()}`);
  await expect(dialog.getByRole("button", { name: "编辑", exact: true })).toBeVisible();
  return candidate.id;
}

test.beforeEach(async ({ request }) => {
  const result = await request.post("/test/skill-files", { data: {} });
  expect(result.ok(), await result.text()).toBeTruthy();
});

async function scopedSkillFixture(request: Page["request"]) {
  const fixture = await request.post("/test/skill-files", { data: {} }).then(response => response.json());
  const accountResponse = await request.post("/api/creators", { data: { display_name: `Skill 优先读取-${crypto.randomUUID()}` } });
  expect(accountResponse.ok(), await accountResponse.text()).toBeTruthy();
  const account = await accountResponse.json();
  for (const skill of [fixture.mind, fixture.production]) {
    const added = await request.post(`/api/creators/${account.id}/skills`, {
      data: { skill_id: skill.id, request_id: crypto.randomUUID() },
    });
    expect(added.ok(), await added.text()).toBeTruthy();
  }
  const series = await request.post("/api/series", { data: {
    name: `Skill 读取栏目-${crypto.randomUUID()}`, creator_id: account.id,
    mind_skill_id: fixture.mind.id, production_skill_id: fixture.production.id,
    description: "", audience: "", request_id: crypto.randomUUID(),
  } });
  expect(series.ok(), await series.text()).toBeTruthy();
  return { account, fixture };
}

test("scoped Skill reads its files and body before starting slow version history", async ({ page, request }) => {
  const { account, fixture } = await scopedSkillFixture(request);
  await page.goto(`/workspace?creator=${account.id}&view=skills`);
  const skill = page.getByRole("region", { name: "账号 Skill 库", exact: true })
    .getByRole("button", { name: /^inspector-mind/ });
  await expect(skill).toBeVisible();

  let releaseFiles!: () => void, releaseBody!: () => void, releaseHistory!: () => void;
  const fileGate = new Promise<void>(resolve => { releaseFiles = resolve; });
  const bodyGate = new Promise<void>(resolve => { releaseBody = resolve; });
  const historyGate = new Promise<void>(resolve => { releaseHistory = resolve; });
  let filesStarted = false, bodyStarted = false, historyStarted = false;
  const base = `/api/producer-skills/${fixture.mind.id}`;
  await page.route(url => url.pathname === `${base}/files` && url.searchParams.get("creator_id") === account.id,
    async route => { filesStarted = true; await fileGate; await route.continue().catch(() => {}); });
  await page.route(url => url.pathname === `${base}/files/content` && url.searchParams.get("creator_id") === account.id,
    async route => { bodyStarted = true; await bodyGate; await route.continue().catch(() => {}); });
  await page.route(url => url.pathname === `${base}/versions` && url.searchParams.get("creator_id") === account.id,
    async route => { historyStarted = true; await historyGate; await route.continue().catch(() => {}); });
  try {
    await skill.click();
    const dialog = page.getByRole("dialog", { name: "inspector-mind", exact: true });
    await expect(dialog).toBeVisible();
    await expect.poll(() => filesStarted).toBe(true);
    expect(historyStarted).toBe(false);
    releaseFiles();
    await expect.poll(() => bodyStarted).toBe(true);
    expect(historyStarted).toBe(false);
    releaseBody();
    await expect(dialog.getByText("完整 Skill 结尾标记", { exact: true })).toBeVisible();
    await expect.poll(() => historyStarted).toBe(true);
    await expect(dialog.getByText("完整 Skill 结尾标记", { exact: true })).toBeVisible();
    releaseHistory();
    await expect(dialog.getByRole("combobox", { name: "查看 Skill 版本" })).toHaveValue(fixture.mind.id);
  } finally { releaseFiles(); releaseBody(); releaseHistory(); }
});

test("scoped Skill file failure still allows switching to another version", async ({ page, request }) => {
  const { account, fixture } = await scopedSkillFixture(request);
  const made = await request.post(`/api/producer-skills/${fixture.mind.id}/versions`, {
    data: { expected_digest: fixture.mind.digest, request_id: crypto.randomUUID() },
  });
  expect(made.ok(), await made.text()).toBeTruthy();
  const candidate = await made.json() as { id: string };
  await page.goto(`/workspace?creator=${account.id}&view=skills`);
  const skill = page.getByRole("region", { name: "账号 Skill 库", exact: true })
    .getByRole("button", { name: /^inspector-mind/ });
  await expect(skill).toBeVisible();
  await page.route(url => url.pathname === `/api/producer-skills/${fixture.mind.id}/files`
    && url.searchParams.get("creator_id") === account.id,
  route => route.fulfill({ status: 503, json: { error: { message: "受控文件读取失败" } } }));
  const writes: string[] = [];
  page.on("request", item => { if (item.method() !== "GET") writes.push(new URL(item.url()).pathname); });
  await skill.click();
  const dialog = page.getByRole("dialog", { name: "inspector-mind", exact: true });
  await expect(dialog.getByRole("alert").filter({ hasText: "受控文件读取失败" })).toBeVisible();
  const selector = dialog.getByRole("combobox", { name: "查看 Skill 版本" });
  await expect(selector).toBeVisible();
  await selector.selectOption(candidate.id);
  await expect(dialog.getByText("完整 Skill 结尾标记", { exact: true })).toBeVisible();
  expect(writes).toEqual([]);
});

test("a stalled file read can be bypassed to choose another Skill version", async ({ page, request }) => {
  const { account, fixture } = await scopedSkillFixture(request);
  const made = await request.post(`/api/producer-skills/${fixture.mind.id}/versions`, {
    data: { expected_digest: fixture.mind.digest, request_id: crypto.randomUUID() },
  });
  expect(made.ok(), await made.text()).toBeTruthy();
  const candidate = await made.json() as { id: string };
  await page.goto(`/workspace?creator=${account.id}&view=skills`);
  const skill = page.getByRole("region", { name: "账号 Skill 库", exact: true })
    .getByRole("button", { name: /^inspector-mind/ });
  await expect(skill).toBeVisible();
  let release!: () => void, blocked = false;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route(url => url.pathname === `/api/producer-skills/${fixture.mind.id}/files`
    && url.searchParams.get("creator_id") === account.id, async route => {
    blocked = true; await gate; await route.continue().catch(() => {});
  });
  try {
    await skill.click();
    const dialog = page.getByRole("dialog", { name: "inspector-mind", exact: true });
    await expect.poll(() => blocked).toBe(true);
    await dialog.getByRole("button", { name: "先查看版本", exact: true }).click();
    const selector = dialog.getByRole("combobox", { name: "查看 Skill 版本" });
    await expect(selector).toBeVisible();
    await selector.selectOption(candidate.id);
    await expect(dialog.getByText("完整 Skill 结尾标记", { exact: true })).toBeVisible();
  } finally { release(); }
});

test("early Skill detail opens before the catalog completes", async ({ page, request }) => {
  const fixture = await request.post("/test/skill-files", { data: {} }).then(response => response.json());
  const made = await request.post(`/api/producer-skills/${fixture.mind.id}/versions`,
    { data: { expected_digest: fixture.mind.digest, request_id: crypto.randomUUID() } });
  expect(made.ok(), await made.text()).toBeTruthy();
  const candidate = await made.json() as { id: string };
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let catalogBlocked = false, historyGets = 0;
  await page.route(url => url.pathname === "/api/producer-skills", async route => {
    catalogBlocked = true;
    await gate;
    try { await route.continue(); } catch { /* page may close on failure */ }
  });
  page.on("request", req => {
    if (req.method() === "GET" && new URL(req.url()).pathname === `/api/producer-skills/${candidate.id}/versions`) historyGets++;
  });
  try {
    await page.goto(`/skills?skill=${candidate.id}`);
    await expect.poll(() => catalogBlocked).toBe(true);
    const card = page.getByRole("dialog", { name: "inspector-mind", exact: true });
    await expect(card).toBeVisible({ timeout: 5000 });
    await expect(card.getByRole("combobox", { name: "查看 Skill 版本" })).toHaveValue(candidate.id);
    await expect(card.getByText("完整 Skill 结尾标记", { exact: true })).toBeVisible();
    expect(historyGets).toBe(1);
    release();
    await expect(page.getByRole("heading", { name: "Skill 库", exact: true })).toBeVisible();
  } finally { release(); }
});

test("early Skill editor keeps its draft when the catalog finishes loading", async ({ page, request }) => {
  const fixture = await request.post("/test/skill-files", { data: {} }).then(response => response.json());
  const made = await request.post(`/api/producer-skills/${fixture.mind.id}/versions`,
    { data: { expected_digest: fixture.mind.digest, request_id: crypto.randomUUID() } });
  expect(made.ok(), await made.text()).toBeTruthy();
  const candidate = await made.json() as { id: string };
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let blocked = false, fileReads = 0;
  await page.route(url => url.pathname === "/api/producer-skills", async route => {
    blocked = true;
    await gate;
    try { await route.continue(); } catch { /* The page may already be closed. */ }
  });
  page.on("request", req => {
    if (req.method() === "GET" && new URL(req.url()).pathname === `/api/producer-skills/${candidate.id}/files`) fileReads++;
  });
  try {
    await page.goto(`/skills?skill=${candidate.id}`);
    await expect.poll(() => blocked).toBe(true);
    const card = page.getByRole("dialog", { name: "inspector-mind", exact: true });
    await card.getByRole("button", { name: "编辑", exact: true }).click();
    const editor = card.getByRole("textbox", { name: "编辑 SKILL.md" });
    const draft = `${await editor.inputValue()}\n目录加载时的未保存草稿。`;
    await editor.fill(draft);
    const original = await card.elementHandle();
    expect(original).not.toBeNull();
    release();
    await expect(page.getByRole("heading", { name: "Skill 库", exact: true })).toBeVisible();
    await expect.poll(() => original!.evaluate(element => element.isConnected)).toBe(true);
    await expect(editor).toHaveValue(draft);
    expect(fileReads).toBe(1);
  } finally { release(); }
});

test("manual Skill selection waits for the chosen version to be verified", async ({ page, request }) => {
  const fixture = await request.post("/test/skill-files", { data: {} }).then(response => response.json());
  let attempts = 0, fileReads = 0;
  await page.route(`**/api/producer-skills/${fixture.mind.id}/versions`, route => {
    attempts++;
    if (attempts === 1) return route.fulfill({ status: 503, json: { detail: "受控版本故障" } });
    return route.continue();
  });
  page.on("request", req => {
    if (req.method() === "GET" && new URL(req.url()).pathname === `/api/producer-skills/${fixture.mind.id}/files`) fileReads++;
  });
  await page.goto("/skills");
  await page.getByRole("searchbox", { name: "搜索 Skill" }).fill("inspector-mind");
  await page.getByRole("button", { name: "inspector-mind", exact: true }).click();
  const error = page.getByRole("alert").filter({ hasText: "无法读取指定 Skill" });
  await expect(error).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(fileReads).toBe(0);
  await error.getByRole("button", { name: "重试读取 Skill", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "inspector-mind", exact: true })).toBeVisible();
});

test("a failed version refresh keeps the editor draft read only until rechecked", async ({ page, request }) => {
  const fixture = await request.post("/test/skill-files", { data: {} }).then(response => response.json());
  const made = await request.post(`/api/producer-skills/${fixture.mind.id}/versions`,
    { data: { expected_digest: fixture.mind.digest, request_id: crypto.randomUUID() } });
  expect(made.ok(), await made.text()).toBeTruthy();
  const candidate = await made.json() as { id: string };
  await page.clock.install();
  await page.goto(`/skills?skill=${candidate.id}`);
  const card = page.getByRole("dialog", { name: "inspector-mind", exact: true });
  await card.getByRole("button", { name: "编辑", exact: true }).click();
  const editor = card.getByRole("textbox", { name: "编辑 SKILL.md" });
  const draft = `${await editor.inputValue()}\n版本读取失败时保留的草稿。`;
  await editor.fill(draft);
  let attempts = 0;
  await page.route(`**/api/producer-skills/${candidate.id}/versions`, route => {
    attempts++;
    return attempts === 1 ? route.fulfill({ status: 503, json: { detail: "受控版本故障" } }) : route.continue();
  });
  await page.clock.fastForward(6000);
  const failed = page.waitForResponse(response => new URL(response.url()).pathname === `/api/producer-skills/${candidate.id}/versions`
    && response.status() === 503);
  await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
  await failed;
  const warning = card.getByRole("alert").filter({ hasText: "当前 Skill 版本暂时无法确认" });
  await expect(warning).toBeVisible();
  await expect(editor).toHaveValue(draft);
  await expect(card.getByRole("button", { name: "保存文件", exact: true })).toBeDisabled();
  await warning.getByRole("button", { name: "重新读取版本", exact: true }).click();
  await expect(warning).toHaveCount(0);
  await expect(editor).toHaveValue(draft);
  await expect(card.getByRole("button", { name: "保存文件", exact: true })).toBeEnabled();
});

test("Skill library manual selection replaces a failed deep link", async ({ page, request }) => {
  const fixture = await request.post("/test/skill-files", { data: {} }).then(response => response.json());
  await page.route(`**/api/producer-skills/${fixture.mind.id}/versions`, route =>
    route.fulfill({ status: 503, json: { detail: "受控读取故障" } }));
  await page.goto(`/skills?skill=${fixture.mind.id}&extraction=preserve-existing-task`);
  await expect(page.getByRole("alert").filter({ hasText: "无法读取指定 Skill" })).toBeVisible();
  await page.getByRole("searchbox", { name: "搜索 Skill" }).fill("inspector-visual");
  await page.getByRole("button", { name: "inspector-visual", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "inspector-visual", exact: true });
  await expect(dialog).toBeVisible();
  await expect.poll(() => new URL(page.url()).searchParams.get("skill")).toBe(fixture.production.id);
  expect(new URL(page.url()).searchParams.get("extraction")).toBe("preserve-existing-task");
  await page.reload();
  await expect(page.getByRole("dialog", { name: "inspector-visual", exact: true })).toBeVisible();
});

test("Skill library manual selection wins over a slow valid deep link", async ({ page, request }) => {
  const fixture = await request.post("/test/skill-files", { data: {} }).then(response => response.json());
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let blocked = false;
  await page.route(`**/api/producer-skills/${fixture.mind.id}/versions`, async route => {
    blocked = true;
    await gate;
    try { await route.continue(); } catch { /* The old target may have been cancelled. */ }
  });
  try {
    await page.goto(`/skills?skill=${fixture.mind.id}`);
    await expect.poll(() => blocked).toBe(true);
    await page.getByRole("searchbox", { name: "搜索 Skill" }).fill("inspector-visual");
    await page.getByRole("button", { name: "inspector-visual", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "inspector-visual", exact: true });
    await expect(dialog).toBeVisible();
    await expect.poll(() => new URL(page.url()).searchParams.get("skill")).toBe(fixture.production.id);
    release();
    await expect(dialog).toBeVisible();
    expect(new URL(page.url()).searchParams.get("skill")).toBe(fixture.production.id);
    await page.reload();
    await expect(page.getByRole("dialog", { name: "inspector-visual", exact: true })).toBeVisible();
  } finally { release(); }
});

test("Skill library opens another card after a successfully opened deep link", async ({ page, request }) => {
  const fixture = await request.post("/test/skill-files", { data: {} }).then(response => response.json());
  await page.goto(`/skills?skill=${fixture.mind.id}`);
  const first = page.getByRole("dialog", { name: "inspector-mind", exact: true });
  await expect(first).toBeVisible();
  await first.getByRole("button", { name: "关闭 Skill 详情", exact: true }).click();
  await expect(first).toHaveCount(0);
  await page.getByRole("searchbox", { name: "搜索 Skill" }).fill("inspector-visual");
  await page.getByRole("button", { name: "inspector-visual", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "inspector-visual", exact: true })).toBeVisible();
  await expect.poll(() => new URL(page.url()).searchParams.get("skill")).toBe(fixture.production.id);
});

test("Skill library opens a chosen Skill from the composer after a failed deep link", async ({ page, request }) => {
  const fixture = await request.post("/test/skill-files", { data: {} }).then(response => response.json());
  await page.route(`**/api/producer-skills/${fixture.mind.id}/versions`, route =>
    route.fulfill({ status: 503, json: { detail: "受控读取故障" } }));
  await page.goto("/skills");
  await page.evaluate(id => sessionStorage.setItem("creatoros:skill-composer:v1",
    JSON.stringify({ ids: [id], name: "", creatorId: "", instruction: "", mode: "reference" })), fixture.production.id);
  await page.goto(`/skills?skill=${fixture.mind.id}`);
  await expect(page.getByRole("alert").filter({ hasText: "无法读取指定 Skill" })).toBeVisible();
  await page.getByRole("button", { name: "查看 inspector-visual", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "inspector-visual", exact: true })).toBeVisible();
  await expect.poll(() => new URL(page.url()).searchParams.get("skill")).toBe(fixture.production.id);
});

test("early Skill detail shows read failures and retries without enabling an unverified target", async ({ page, request }) => {
  const fixture = await request.post("/test/skill-files", { data: {} }).then(response => response.json());
  const made = await request.post(`/api/producer-skills/${fixture.mind.id}/versions`,
    { data: { expected_digest: fixture.mind.digest, request_id: crypto.randomUUID() } });
  expect(made.ok(), await made.text()).toBeTruthy();
  const candidate = await made.json() as { id: string };
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route(url => url.pathname === "/api/producer-skills", async route => {
    await gate;
    try { await route.continue(); } catch { /* page may close on failure */ }
  });
  let attempts = 0;
  await page.route(`**/api/producer-skills/${candidate.id}/versions`, route => {
    attempts++;
    if (attempts <= 2) return route.fulfill({ status: attempts === 1 ? 503 : 404, json: { detail: "受控读取故障" } });
    return route.continue();
  });
  try {
    await page.goto(`/skills?skill=${candidate.id}`);
    const error = page.getByRole("alert").filter({ hasText: "无法读取指定 Skill" });
    await expect(error).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await error.getByRole("button", { name: "重试读取 Skill", exact: true }).click();
    await expect.poll(() => attempts).toBe(2);
    await expect(error).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await error.getByRole("button", { name: "重试读取 Skill", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "inspector-mind", exact: true })).toBeVisible();
  } finally { release(); }
});

test("early Skill detail ignores a late previous target and keeps builtin lookup unchanged", async ({ page, request }) => {
  const fixture = await request.post("/test/skill-files", { data: {} }).then(response => response.json());
  const create = async () => {
    const response = await request.post(`/api/producer-skills/${fixture.mind.id}/versions`,
      { data: { expected_digest: fixture.mind.digest, request_id: crypto.randomUUID() } });
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json() as Promise<{ id: string }>;
  };
  const first = await create(), second = await create();
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let pending = false;
  await page.route(`**/api/producer-skills/${first.id}/versions`, async route => {
    pending = true; await gate;
    try { await route.continue(); } catch { /* old target may already be aborted */ }
  });
  try {
    await page.goto(`/skills?skill=${first.id}`);
    await expect.poll(() => pending).toBe(true);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.getByRole("status").filter({ hasText: "正在读取指定 Skill" })).toBeVisible();
    await page.evaluate(id => { history.pushState({}, "", `/skills?skill=${id}`); window.dispatchEvent(new PopStateEvent("popstate")); }, second.id);
    const card = page.getByRole("dialog", { name: "inspector-mind", exact: true });
    await expect(card.getByRole("combobox", { name: "查看 Skill 版本" })).toHaveValue(second.id);
    release();
    await expect(card.getByRole("combobox", { name: "查看 Skill 版本" })).toHaveValue(second.id);
    expect(new URL(page.url()).searchParams.get("skill")).toBe(second.id);
  } finally { release(); }
  await page.unrouteAll({ behavior: "wait" });
  let builtinHistory = 0;
  page.on("request", req => {
    if (new URL(req.url()).pathname === "/api/producer-skills/knowledge-to-carousel/versions") builtinHistory++;
  });
  await page.goto("/skills?skill=knowledge-to-carousel");
  await expect(page.getByRole("dialog", { name: "knowledge-to-carousel", exact: true })).toBeVisible();
  expect(builtinHistory).toBe(0);
});

test("real files: full Markdown, source, script, image, local links; viewing never writes", async ({ page }, info) => {
  const writes: string[] = [];
  const external: string[] = [];
  page.on("request", req => {
    if (new URL(req.url()).pathname.startsWith("/api/") && req.method() !== "GET") writes.push(req.url());
    if (req.url().startsWith("https://example.invalid")) external.push(req.url());
  });
  const dialog = await openMind(page);
  await expect(page.getByLabel("Skill 组合框", { exact: true })).not.toContainText("inspector-mind");
  await expect(dialog.getByText("将新主题变成可复用的内容方法与图解。", { exact: true })).toBeVisible();
  const tree = dialog.getByRole("navigation", { name: "Skill 文件结构" });
  await expect(tree.getByRole("button", { name: "SKILL.md", exact: true })).toHaveAttribute("aria-current", "page");
  await expect(dialog.getByRole("img", { name: "assets/diagram.png" })).toHaveJSProperty("naturalWidth", 320);
  await expect(dialog.locator(".skill-file-markdown p div, .skill-file-markdown p p")).toHaveCount(0);
  await page.screenshot({ path: info.outputPath("skill-desktop.png") });
  await dialog.getByRole("button", { name: "源码", exact: true }).click();
  await expect(dialog.locator("pre")).toContainText("name: inspector-mind");
  await expect(dialog.locator("pre")).toContainText("完整 Skill 结尾标记");
  await dialog.getByRole("button", { name: "阅读", exact: true }).click();
  await dialog.getByRole("button", { name: "查看脚本", exact: true }).click();
  await expect(dialog.locator("pre")).toHaveText("# 仅供查看，不应执行\nraise RuntimeError('SCRIPT_MUST_NOT_EXECUTE')\n");
  await tree.getByRole("button", { name: "diagram.png", exact: true }).click();
  await expect(dialog.getByRole("img")).toHaveJSProperty("naturalWidth", 320);
  await tree.getByRole("button", { name: "readme.md", exact: true }).click();
  await dialog.getByRole("button", { name: "返回正文", exact: true }).click();
  await expect(tree.getByRole("button", { name: "SKILL.md", exact: true })).toHaveAttribute("aria-current", "page");
  await tree.getByRole("button", { name: "manual.pdf", exact: true }).click();
  await expect(dialog.getByText(/不支持预览/)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: "inspector-mind", exact: true })).toBeFocused();
  expect(writes).toEqual([]);
  expect(external).toEqual([]);
});

test("Markdown image preview can retry without invalid nested blocks", async ({ page }) => {
  let imageRequests = 0;
  let imageAvailable = false;
  await page.route("**/api/producer-skills/*/files/content?*", async route => {
    if (new URL(route.request().url()).searchParams.get("path") !== "assets/diagram.png") return route.continue();
    imageRequests++;
    if (!imageAvailable) return route.fulfill({ status: 404, body: "missing" });
    return route.continue();
  });
  const dialog = await openMind(page);
  const preview = dialog.locator(".skill-file-markdown .skill-image-preview");
  await expect(preview.getByRole("alert")).toContainText("图片无法读取");
  await expect(dialog.locator(".skill-file-markdown p div, .skill-file-markdown p p")).toHaveCount(0);
  imageAvailable = true;
  await preview.getByRole("button", { name: "重新加载图片" }).click();
  await expect(preview.getByRole("img")).toHaveJSProperty("naturalWidth", 320);
  expect(imageRequests).toBeGreaterThanOrEqual(2);
});

test("add is explicit; filled slot can inspect without clearing; keyboard remains inside modal", async ({ page }) => {
  const dialog = await openMind(page);
  for (let i = 0; i < 15; i++) {
    await page.keyboard.press("Tab");
    expect(await dialog.evaluate(node => node.contains(document.activeElement))).toBeTruthy();
  }
  await dialog.getByRole("button", { name: "加入组合", exact: true }).click();
  const slot = page.getByLabel("Skill 组合框", { exact: true });
  await expect(slot).toContainText("inspector-mind");
  await page.getByRole("button", { name: "查看 inspector-mind", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "关闭 Skill 详情", exact: true }).click();
  await expect(slot).toContainText("inspector-mind");
  await page.getByRole("button", { name: "移除 inspector-mind", exact: true }).click();
  await expect(slot).not.toContainText("inspector-mind");
  await page.getByRole("button", { name: "knowledge-to-carousel", exact: true }).click();
  await expect(page.getByRole("dialog").getByRole("heading", { name: "knowledge-to-carousel" })).toBeVisible();
  await expect(page.getByRole("dialog").getByRole("button", { name: "加入组合" })).toBeVisible();
  await expect(page.getByRole("dialog").getByRole("button", { name: "编辑", exact: true })).toHaveCount(0);
});

test("drag selects the free basket without opening inspector", async ({ page }) => {
  await limitVisibleSkillCatalog(page, ["inspector-mind", "inspector-visual", "knowledge-to-carousel"]);
  await page.goto("/skills");
  const card = page.getByRole("button", { name: "inspector-visual", exact: true });
  const slot = page.getByLabel("Skill 组合框", { exact: true });
  const start = await card.boundingBox(), finish = await slot.boundingBox();
  expect(start && finish).toBeTruthy();
  await page.mouse.move(start!.x + start!.width / 2, start!.y + start!.height / 2);
  await page.mouse.down();
  await page.mouse.move(finish!.x + finish!.width / 2, finish!.y + finish!.height / 2, { steps: 15 });
  await page.mouse.up();
  await expect(slot).toContainText("inspector-visual");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await card.click();
  await expect(page.getByRole("dialog", { name: "inspector-visual" })).toBeVisible();
});

test("mobile: collapsible files, readable preview, no viewport overflow", async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const dialog = await openMind(page);
  await dialog.getByRole("navigation").getByRole("button", { name: "example.py", exact: true }).click();
  await expect(dialog.locator("pre")).toContainText("SCRIPT_MUST_NOT_EXECUTE");
  await dialog.locator(".skill-files-trigger").click();
  await expect(dialog.getByRole("navigation")).not.toBeVisible();
  await page.screenshot({ path: info.outputPath("skill-mobile.png") });
  const rect = await dialog.boundingBox();
  expect(rect!.x).toBeGreaterThanOrEqual(0);
  expect(rect!.x + rect!.width).toBeLessThanOrEqual(390);
  expect(rect!.y + rect!.height).toBeLessThanOrEqual(844);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.getByRole("button", { name: "关闭 Skill 详情", exact: true }).click();
  await expect(dialog).toHaveCount(0);
});

test("controlled read failure can retry against real API", async ({ page }) => {
  let reads = 0;
  await page.route("**/api/producer-skills/*/files", async route => {
    reads += 1;
    if (reads === 1) { await route.fulfill({ status: 503, json: { error: { message: "临时读取失败" } } }); }
    else await route.continue();
  });
  await page.goto("/skills");
  await page.getByRole("searchbox", { name: "搜索 Skill" }).fill("inspector-mind");
  await page.getByRole("button", { name: "inspector-mind", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("alert")).toContainText("临时读取失败");
  expect(reads).toBe(1);
  await dialog.getByRole("button", { name: "重新读取", exact: true }).click();
  await expect(dialog.getByText("完整 Skill 结尾标记", { exact: true })).toBeAttached();
  expect(reads).toBe(2);
});

test("controlled delayed old file cannot replace new preview", async ({ page }) => {
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let held!: () => void;
  const started = new Promise<void>(resolve => { held = resolve; });
  await page.route("**/api/producer-skills/*/files/content?*", async route => {
    if (new URL(route.request().url()).searchParams.get("path") === "scripts/example.py") {
      const result = await route.fetch();
      held();
      await gate;
      try { await route.fulfill({ response: result }); } catch { /* Switching files aborts this request. */ }
    } else await route.continue();
  });
  const dialog = await openMind(page);
  const tree = dialog.getByRole("navigation");
  await tree.getByRole("button", { name: "example.py", exact: true }).click();
  await started;
  await tree.getByRole("button", { name: "readme.md", exact: true }).click();
  await expect(dialog.getByRole("heading", { name: "文件关系" })).toBeVisible();
  release();
  await expect(tree.getByRole("button", { name: "readme.md", exact: true })).toHaveAttribute("aria-current", "page");
  await expect(dialog.locator("pre")).toHaveCount(0);
});

test("edit saves the local text file and confirms before discarding a draft", async ({ page }, info) => {
  const dialog = await openMind(page);
  const candidateId = await createCandidate(page, dialog);
  await dialog.getByRole("button", { name: "源码", exact: true }).click();
  const original = await dialog.locator(".skill-file-source").innerText();
  await dialog.getByRole("button", { name: "编辑", exact: true }).click();
  const editor = dialog.getByRole("textbox", { name: "编辑 SKILL.md" });
  await editor.fill(`${original}\n\n前端编辑验收标记。`);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.screenshot({ path: info.outputPath("skill-edit-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: info.outputPath("skill-edit-mobile.png") });
  await page.setViewportSize({ width: 1440, height: 900 });
  page.once("dialog", nativeDialog => nativeDialog.dismiss());
  await dialog.getByRole("navigation").getByRole("button", { name: "example.py", exact: true }).click();
  await expect(editor).toBeVisible();
  expect(await dialog.getByRole("navigation").getByRole("button", { name: "SKILL.md", exact: true }).getAttribute("aria-current")).toBe("page");
  await dialog.getByRole("button", { name: "保存文件", exact: true }).click();
  await expect(dialog.locator(".skill-file-source")).toContainText("前端编辑验收标记。");
  await page.reload();
  const reloaded = await openMind(page);
  await expect(reloaded.getByText("前端编辑验收标记。", { exact: true })).toHaveCount(0);
  await reloaded.getByRole("combobox", { name: "查看 Skill 版本" }).selectOption(candidateId);
  await expect(reloaded.getByText("前端编辑验收标记。", { exact: true })).toBeVisible();
});

test("409 keeps the user's draft until they inspect and explicitly overwrite the latest file", async ({ page }) => {
  let conflict = true;
  let currentTextDigest = "";
  const saveDigests: string[] = [];
  await page.route("**/api/producer-skills/*/files/content*", async route => {
    if (route.request().method() === "PUT") {
      saveDigests.push(route.request().postDataJSON().expected_digest);
      if (conflict) {
        conflict = false;
        await route.fulfill({ status: 409, json: { error: { code: "skill_digest_conflict", message: "Skill 已在其他位置修改。", current_digest: "new-directory-digest" } } });
      } else await route.continue();
    } else if (new URL(route.request().url()).searchParams.get("path") === "SKILL.md") {
      const response = await route.fetch();
      const body = await response.json();
      currentTextDigest = body.digest;
      await route.fulfill({ response, json: body });
    } else await route.continue();
  });
  const dialog = await openMind(page);
  await createCandidate(page, dialog);
  await dialog.getByRole("button", { name: "编辑", exact: true }).click();
  const editor = dialog.getByRole("textbox", { name: "编辑 SKILL.md" });
  const draft = `${await editor.inputValue()}\n\n冲突草稿仍在。`;
  await editor.fill(draft);
  await dialog.getByRole("button", { name: "保存文件", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("Skill 已在其他位置修改");
  await expect(editor).toHaveValue(draft);
  await dialog.getByRole("button", { name: "读取当前版本并保留草稿" }).click();
  await expect(dialog.getByText(/已读取服务器当前版本；你的草稿仍保留。/, { exact: false })).toBeVisible();
  await expect(editor).toHaveValue(draft);
  const overwriteExpectedDigest = currentTextDigest;
  await dialog.getByRole("button", { name: "覆盖保存", exact: true }).click();
  await expect(dialog.getByText("冲突草稿仍在。", { exact: true })).toBeVisible();
  expect(saveDigests[1]).toBe(overwriteExpectedDigest);
  await page.unrouteAll({ behavior: "wait" });
});

test("Skill change request enters the existing Agent composer as a draft without sending", async ({ page }) => {
  const writes: string[] = [];
  page.on("request", req => { if (req.url().includes("/api/agent/sessions") && req.method() !== "GET") writes.push(req.url()); });
  const dialog = await openMind(page);
  await dialog.getByRole("button", { name: "让 Agent 帮我修改当前 Skill", exact: true }).click();
  await dialog.getByRole("textbox", { name: "让 Agent 修改 inspector-mind" }).fill("保留现有结构，把目标读者调整为刚入门的创作者。");
  await dialog.getByRole("button", { name: "在 Agent 中继续", exact: true }).click();
  await expect(page).toHaveURL(/\/skills/);
  await expect(page.locator(".account-chat-panel")).toBeVisible();
  const composer = page.getByRole("textbox", { name: "给 Agent 的消息" });
  await expect(composer).toHaveValue(/inspector-mind[\s\S]*保留现有结构/);
  await expect(composer).toHaveValue(/目标文件：SKILL\.md/);
  expect(writes).toEqual([]);
});

test("prefilling a Skill edit never silently discards an unsent Agent draft", async ({ page }) => {
  const dialog = await openMind(page);
  await page.evaluate(() => sessionStorage.setItem("creatoros.agent.draft.v1:overview", "原来的草稿，不能静默覆盖。"));
  let writes = 0;
  page.on("request", event => { if (event.method() === "POST") writes++; });
  await dialog.getByRole("button", { name: "让 Agent 帮我修改当前 Skill", exact: true }).click();
  await dialog.getByRole("textbox", { name: "让 Agent 修改 inspector-mind" }).fill("新的修改要求");
  page.once("dialog", async confirmation => { expect(confirmation.message()).toContain("已有未发送"); await confirmation.dismiss(); });
  await dialog.getByRole("button", { name: "在 Agent 中继续", exact: true }).click();
  const panel = page.locator(".account-chat-panel");
  await expect(panel).toBeVisible();
  await expect(panel.getByRole("textbox", { name: "给 Agent 的消息" })).toHaveValue("原来的草稿，不能静默覆盖。");
  expect(writes).toBe(0);
});

test("workspace Skill editing stays in the account drawer and survives refresh without sending", async ({ page, request }, info) => {
  const seed = await request.post("/test/skill-files", { data: {} });
  const fixture = await seed.json() as { mind: { id: string }; production: { id: string } };
  const created = await request.post("/api/creators", { data: { display_name: `改稿账号-${crypto.randomUUID()}` } });
  const account = await created.json() as { id: string; display_name: string };
  const composed = await request.post("/api/series", { data: { name: "账号内改稿", creator_id: account.id,
    mind_skill_id: fixture.mind.id, production_skill_id: fixture.production.id,
    description: "", audience: "", request_id: crypto.randomUUID().replaceAll("-", "") } });
  expect(composed.ok(), await composed.text()).toBeTruthy();
  const { series } = await composed.json() as { series: { id: string } };
  // Current column headers read the account library and column pool, rather
  // than the legacy composition binding alone.
  const added = await request.post(`/api/creators/${account.id}/skills`, { data: { skill_id: fixture.mind.id, request_id: crypto.randomUUID() } });
  expect(added.ok(), await added.text()).toBeTruthy();
  const pool = await request.get(`/api/series/${series.id}/skills`).then(response => response.json());
  const updated = await request.put(`/api/series/${series.id}/skills`, { data: { expected_version: pool.version,
    skill_ids: [...new Set([...pool.items.map((item: { id: string }) => item.id), fixture.mind.id])], defaults: { image: [], video: [] } } });
  expect(updated.ok(), await updated.text()).toBeTruthy();
  let posts = 0;
  page.on("request", req => { if (req.url().includes("/api/agent/sessions") && req.method() !== "GET") posts++; });
  await page.goto(`/?creator=${account.id}&series=${series.id}`);
  await page.getByRole("button", { name: /^inspector-mind\s*v\d+/ }).click();
  const dialog = page.getByRole("dialog", { name: "inspector-mind" });
  await dialog.getByRole("button", { name: "让 Agent 帮我修改当前 Skill" }).click();
  await dialog.getByRole("textbox", { name: "让 Agent 修改 inspector-mind" }).fill("不要额外标题，保留现有参考。请保存。");
  await dialog.getByRole("button", { name: "在 Agent 中继续" }).click();
  await expect(dialog).not.toBeAttached();
  const panel = page.locator(".account-chat-panel");
  await expect(panel.getByRole("heading", { name: account.display_name, exact: true })).toBeVisible();
  const composer = panel.getByRole("textbox", { name: "给 Agent 的消息" });
  await expect(composer).toHaveValue(new RegExp(`账号 ID：${account.id}[\\s\\S]*栏目 ID：${series.id}`));
  await expect(composer).toHaveValue(/不要额外标题/);
  expect(new URL(page.url()).pathname).toBe("/");
  await page.screenshot({ path: info.outputPath("account-skill-edit-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: info.outputPath("account-skill-edit-mobile.png") });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.reload();
  await expect(panel).toBeVisible();
  await expect(composer).toHaveValue(/不要额外标题/);
  expect(posts).toBe(0);
});

test("Run Skill editing carries the displayed revision into its account, not the global Agent", async ({ page, request }) => {
  const fixture = await (await request.post("/test/skill-files", { data: {} })).json() as { mind: { id: string }; production: { id: string } };
  const account = await (await request.post("/api/creators", { data: { display_name: "作品改稿账号" } })).json() as { id: string };
  const composed = await request.post("/api/series", { data: { name: "作品改稿栏目", creator_id: account.id,
    mind_skill_id: fixture.mind.id, production_skill_id: fixture.production.id,
    description: "", audience: "", request_id: crypto.randomUUID().replaceAll("-", "") } });
  expect(composed.ok(), await composed.text()).toBeTruthy();
  const { series } = await composed.json() as { series: { id: string } };
  await page.route("**/api/runs/run-edit-context", route => route.fulfill({ json: {
    id: "run-edit-context", creator_id: account.id, creator_name: "作品改稿账号", series_id: series.id, series_name: "作品改稿栏目",
    topic_id: "example", topic_title: "检查历史作品", status: "approved", version: 3, active_revision_number: 2,
    allowed_actions: [], input_snapshot: { skill_name: fixture.mind.id }, publication: null, partial_cards: [],
    revisions: [{ id: "revision-one", revision_number: 1, cards: [], attempts: [] },
      { id: "revision-two", revision_number: 2, cards: [], attempts: [] }],
  } }));
  await page.route("**/api/runs/run-edit-context/events?*", route => route.fulfill({ json: { items: [], next_after_id: 0 } }));
  await page.route("**/api/runs/run-edit-context/discussion*", route => route.fulfill({ json: { items: [] } }));
  let posts = 0;
  page.on("request", req => { if (req.url().includes("/api/agent/sessions") && req.method() !== "GET") posts++; });
  await page.goto("/runs/run-edit-context?revision=revision-one");
  await page.getByRole("button", { name: "inspector-mind", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "inspector-mind" });
  await dialog.getByRole("button", { name: "让 Agent 帮我修改当前 Skill" }).click();
  await dialog.getByRole("textbox", { name: "让 Agent 修改 inspector-mind" }).fill("看这一版图片再改 Skill。");
  await dialog.getByRole("button", { name: "在 Agent 中继续" }).click();
  const panel = page.locator(".account-chat-panel");
  await expect(panel).toBeVisible();
  await expect(panel.getByRole("textbox", { name: "给 Agent 的消息" })).toHaveValue(/作品 Run ID：run-edit-context[\s\S]*Revision ID：revision-one/);
  expect(new URL(page.url()).pathname).toBe("/runs/run-edit-context");
  expect(new URL(page.url()).searchParams.get("revision")).toBe("revision-one");
  await expect.poll(() => new URL(page.url()).searchParams.has("skill_edit")).toBe(false);
  expect(posts).toBe(0);
});

test("Run detail shows its frozen Skill and opens the current local card", async ({ page, request }, info) => {
  const seeded = await request.post("/test/skill-files", { data: {} });
  expect(seeded.ok(), await seeded.text()).toBeTruthy();
  const fixture = await seeded.json() as { mind: { id: string }; production: { id: string } };
  const created = await request.post("/api/creators", { data: { display_name: "隔离账号" } });
  expect(created.ok(), await created.text()).toBeTruthy();
  const account = await created.json() as { id: string };
  const composed = await request.post("/api/series", { data: { name: "Skill 来源验收", creator_id: account.id,
    mind_skill_id: fixture.mind.id, production_skill_id: fixture.production.id,
    description: "", audience: "", request_id: crypto.randomUUID().replaceAll("-", "") } });
  expect(composed.ok(), await composed.text()).toBeTruthy();
  const { series } = await composed.json() as { series: { id: string } };
  const added = await request.post(`/api/creators/${account.id}/skills`, {
    data: { skill_id: fixture.mind.id, request_id: crypto.randomUUID() },
  });
  expect(added.ok(), await added.text()).toBeTruthy();
  const snapshot = { creator_id: account.id, series_id: series.id, topic_id: "e2e_topic",
    topic_title: "冻结 Skill 版本验收", skill_name: fixture.mind.id, skill_digest: "a".repeat(64) };
  await page.route("**/api/runs/run-skill-fixture", route => route.fulfill({ json: {
    id: "run-skill-fixture", creator_id: account.id, creator_name: "隔离账号", series_id: series.id, series_name: "Skill 来源验收",
    topic_id: "e2e_topic", topic_title: "冻结 Skill 版本验收", status: "approved", version: 3,
    active_revision_number: 1, updated_at: "2026-10-06T00:00:00Z", completed_at: null, heartbeat_at: null,
    lease_expires_at: null, retryable: false, error_stage: null, error_type: null, error_message: null,
    allowed_actions: [], cover_url: null, card_count: 0, input_snapshot: snapshot, producer_thread_id: null,
    revisions: [], events_url: "/api/runs/run-skill-fixture/events", publication: null, production_progress: null, partial_cards: [],
  } }));
  await page.route("**/api/runs/run-skill-fixture/events?*", route => route.fulfill({ json: { items: [], next_after_id: 0 } }));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/runs/run-skill-fixture");
  const used = page.getByRole("region", { name: "本次运行使用的 Skill" });
  await expect(used).toContainText("inspector-mind");
  await expect(used).not.toContainText("aaaaaaaaaaaa…");
  await expect(used).not.toContainText("编辑只影响新 Run");
  await expect(used.getByRole("button", { name: "inspector-mind", exact: true })).toBeVisible();
  await page.getByRole("button", { name: /^技术记录/ }).click();
  await expect(page.getByText("a".repeat(64), { exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath("run-skill-desktop.png"), fullPage: true });
  await used.getByRole("button", { name: "inspector-mind", exact: true }).click();
  const card = page.getByRole("dialog", { name: "inspector-mind" });
  await expect(card).toBeVisible();
  await expect(card.getByRole("button", { name: "编辑", exact: true })).toHaveCount(0);
  await expect(card.getByRole("button", { name: "创建修改候选", exact: true })).toBeVisible();
  await card.getByRole("button", { name: "关闭 Skill 详情" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: info.outputPath("run-skill-mobile.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});

test("Chinese display name belongs to the candidate and never changes the saved version or basket", async ({ page, request }, info) => {
  const seeded = await request.post("/test/skill-files", { data: {} }).then(r => r.json());
  const id = seeded.mind.id;
  const path = `/api/producer-skills/${id}/files/content`;
  const before = await request.get(`${path}?path=SKILL.md`).then(r => r.json());
    await openMind(page);
    await page.getByRole("dialog").getByRole("button", { name: "加入组合", exact: true }).click();
    await page.getByRole("button", { name: "查看 inspector-mind", exact: true }).click();
    const card = page.getByRole("dialog");
    const candidateId = await createCandidate(page, card);
    const candidatePath = `/api/producer-skills/${candidateId}/files/content`;
    await card.getByRole("button", { name: "改名", exact: true }).click();
    const input = card.getByRole("textbox", { name: "Skill 显示名称", exact: true });
    await input.fill("柯基漫画");
    await expect(card.getByRole("button", { name: "编辑", exact: true })).toBeDisabled();
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      const rect = await input.boundingBox(); expect(rect!.x + rect!.width).toBeLessThanOrEqual(width);
      await page.screenshot({ path: info.outputPath(`skill-rename-${width}.png`) });
    }
    await page.setViewportSize({ width: 1440, height: 900 });
    const saved = page.waitForResponse(r => r.request().method() === "PUT" && new URL(r.url()).pathname === candidatePath);
    await card.getByRole("button", { name: "保存名称", exact: true }).click();
    const response = await saved; expect(response.status()).toBe(200);
    await expect(card.getByRole("heading", { name: "柯基漫画", exact: true })).toBeVisible();
    const actual = await request.get(`${candidatePath}?path=SKILL.md`).then(r => r.json());
    expect(actual.content).toContain("name: inspector-mind");
    expect(actual.content).toContain("creatoros-display-name: 柯基漫画");
    expect(actual.content.replaceAll("\r\n", "\n").split("\n---\n")[1]).toBe(before.content.replaceAll("\r\n", "\n").split("\n---\n")[1]);
    await card.getByRole("button", { name: "关闭 Skill 详情", exact: true }).click();
    await expect(page.getByLabel("Skill 组合框", { exact: true })).toContainText("inspector-mind");
    await page.goto(`/skills?skill=${candidateId}`);
    await expect(page.getByRole("dialog", { name: "柯基漫画", exact: true })).toBeVisible();
    expect((await request.get(`${path}?path=SKILL.md`).then(r => r.json())).content).toBe(before.content);
});

test("legal multiline YAML saves and invalid internal names show a real 422 without losing the draft", async ({ page, request }) => {
  const seeded = await request.post("/test/skill-files", { data: {} }).then(r => r.json());
  const originalPath = `/api/producer-skills/${seeded.mind.id}/files/content`;
  const before = await request.get(`${originalPath}?path=SKILL.md`).then(r => r.json());
    const card = await openMind(page);
    const candidateId = await createCandidate(page, card);
    const path = `/api/producer-skills/${candidateId}/files/content`;
    await card.getByRole("button", { name: "编辑", exact: true }).click();
    const editor = card.getByRole("textbox", { name: "编辑 SKILL.md", exact: true });
    const multiline = before.content.replaceAll("\r\n", "\n").replace(/^description:.*$/m, 'description: >-\n  第一行：讲故事\n  第二行也要保留');
    await editor.fill(multiline);
    await card.getByRole("button", { name: "保存文件", exact: true }).click();
    await expect(card.getByText("第一行：讲故事 第二行也要保留", { exact: true })).toBeVisible();
    await card.getByRole("button", { name: "编辑", exact: true }).click();
    const badDraft = multiline.replace("name: inspector-mind", "name: 不该替换内部标识");
    await editor.fill(badDraft);
    await card.getByRole("button", { name: "保存文件", exact: true }).click();
    await expect(card.getByRole("alert")).toContainText("name 必须");
    await expect(editor).toHaveValue(badDraft);
    expect((await request.get(`${path}?path=SKILL.md`).then(r => r.json())).content).toBe(multiline);
    expect((await request.get(`${originalPath}?path=SKILL.md`).then(r => r.json())).content).toBe(before.content);
});

test("a naming conflict keeps the name draft and never silently refreshes the save token", async ({ page }) => {
  let posts = 0;
  await page.route("**/api/producer-skills/*/files/content", async route => {
    if (route.request().method() !== "PUT") return route.continue();
    posts++;
    await route.fulfill({ status: 409, json: { error: { code: "skill_digest_conflict", message: "Skill 已被其他编辑更新。" } } });
  });
  const card = await openMind(page);
  await createCandidate(page, card);
  await card.getByRole("button", { name: "改名", exact: true }).click();
  await card.getByRole("textbox", { name: "Skill 显示名称", exact: true }).fill("保留这个短名称");
  await card.getByRole("button", { name: "保存名称", exact: true }).click();
  await expect(card.getByRole("alert")).toContainText("Skill 已被其他编辑更新");
  await expect(card.getByRole("textbox", { name: "Skill 显示名称", exact: true })).toHaveValue("保留这个短名称");
  expect(posts).toBe(1);
  await card.getByRole("button", { name: "取消", exact: true }).click();
  await expect(card.getByRole("textbox", { name: "Skill 显示名称", exact: true })).toHaveCount(0);
});

test("candidate keeps the full folder, asset changes stay isolated, and refresh can reopen the same candidate", async ({ page, request }, info) => {
  const fixture = await request.post("/test/skill-files", { data: {} }).then(response => response.json());
  const basePath = `/api/producer-skills/${fixture.mind.id}`;
  const before = await request.get(`${basePath}/files`).then(response => response.json());
  const originalImage = await request.get(`${basePath}/files/content?path=assets%2Fdiagram.png`).then(response => response.body());
  const card = await openMind(page);
  const candidateId = await createCandidate(page, card);
  const candidatePath = `/api/producer-skills/${candidateId}`;
  const cloned = await request.get(`${candidatePath}/files`).then(response => response.json());
  expect(cloned.files.map((item: { path: string }) => item.path)).toEqual(before.files.map((item: { path: string }) => item.path));
  await card.getByRole("navigation").getByRole("button", { name: "diagram.png", exact: true }).click();
  const replacement = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aQ1cAAAAASUVORK5CYII=", "base64");
  const chooser = page.waitForEvent("filechooser");
  await card.getByRole("button", { name: "替换图片", exact: true }).click();
  await (await chooser).setFiles({ name: "reference.png", mimeType: "image/png", buffer: replacement });
  await card.getByRole("button", { name: "保存图片", exact: true }).click();
  await expect(card.getByRole("img", { name: "assets/diagram.png" })).toHaveJSProperty("naturalWidth", 1);
  expect(await request.get(`${basePath}/files/content?path=assets%2Fdiagram.png`).then(response => response.body())).toEqual(originalImage);
  expect((await request.get(`${basePath}/files`).then(response => response.json())).digest).toBe(before.digest);
  await expect(card.getByRole("button", { name: "保存版本", exact: true })).toBeEnabled();
  await expect(card.getByRole("textbox", { name: "版本试用选题" })).toHaveCount(0);
  await expect(card.getByRole("button", { name: "干净线程试用", exact: true })).toHaveCount(0);
  await page.screenshot({ path: info.outputPath("skill-version-candidate-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: info.outputPath("skill-version-candidate-mobile.png") });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.goto(`/skills?skill=${candidateId}`);
  await expect(page.getByRole("dialog").getByRole("combobox", { name: "查看 Skill 版本" })).toHaveValue(candidateId);
  await page.getByRole("dialog").getByRole("navigation").getByRole("button", { name: "diagram.png", exact: true }).click();
  await expect(page.getByRole("dialog").getByRole("img", { name: "assets/diagram.png" })).toHaveJSProperty("naturalWidth", 1);
});

test("unknown candidate creation survives refresh and is checked without a second write", async ({ page, request }) => {
  const fixture = await request.post("/test/skill-files", { data: {} }).then(response => response.json());
  const versionsPath = `/api/producer-skills/${fixture.mind.id}/versions`;
  const before = await request.get(versionsPath).then(response => response.json());
  const ids: string[] = [];
  let fail = true;
  await page.route(`**${versionsPath}`, async route => {
    if (route.request().method() !== "POST") return route.continue();
    ids.push(route.request().postDataJSON().request_id);
    const actual = await route.fetch();
    if (fail) { fail = false; await route.fulfill({ status: 503, json: { error: { message: "模拟响应丢失，请重试核对" } } }); }
    else await route.fulfill({ response: actual });
  });
  const card = await openMind(page);
  await card.getByRole("button", { name: "创建修改候选", exact: true }).click();
  await expect(card.getByText(/模拟响应丢失/)).toBeVisible();
  await page.reload();
  const reopened = await openMind(page);
  await expect(reopened.getByRole("button", { name: "创建修改候选", exact: true })).toBeDisabled();
  await reopened.getByRole("button", { name: "核对创建结果", exact: true }).click();
  await reopened.getByRole("button", { name: "v2 · 候选", exact: true }).click();
  await expect(reopened.getByText(/无法确认它就是本次创建的候选/)).toBeVisible();
  page.once("dialog", confirmation => confirmation.accept());
  await reopened.getByRole("button", { name: "已核对，解除锁定", exact: true }).click();
  await expect(reopened.getByRole("button", { name: "编辑", exact: true })).toBeVisible();
  expect(ids).toHaveLength(1);
  const after = await request.get(versionsPath).then(response => response.json());
  expect(after.items).toHaveLength(before.items.length + 1);
});

test("candidate saves, switches and rolls back without any trial or production", async ({ page, request }, info) => {
  const fixture = await request.post("/test/skill-files", { data: {} }).then(response => response.json());
  const created = await request.post("/api/series", { data: { name: `版本选题验收-${crypto.randomUUID().slice(0, 8)}`,
    mind_skill_id: fixture.mind.id, production_skill_id: fixture.production.id, description: "", audience: "", request_id: crypto.randomUUID() } });
  expect(created.ok(), await created.text()).toBeTruthy();
  const series = (await created.json()).series;
  const otherResult = await request.post("/api/series", { data: { name: `不切换栏目-${crypto.randomUUID().slice(0, 8)}`,
    mind_skill_id: fixture.mind.id, production_skill_id: fixture.production.id, description: "", audience: "", request_id: crypto.randomUUID() } });
  expect(otherResult.ok(), await otherResult.text()).toBeTruthy();
  const other = (await otherResult.json()).series;
  await page.goto(`/skills?skill=${fixture.mind.id}`);
  const card = page.getByRole("dialog");
  const candidateId = await createCandidate(page, card);
  let trialPosts = 0, productionPosts = 0;
  const saves: Record<string, unknown>[] = [], switches: Record<string, unknown>[] = [];
  page.on("request", req => {
    if (req.method() !== "POST") return;
    const url = new URL(req.url()).pathname;
    if (url.includes("/version-trials") || url.endsWith("/trials")) trialPosts++;
    if (url.startsWith("/api/runs")) productionPosts++;
    if (url.endsWith("/publish-version")) saves.push(req.postDataJSON());
    if (url.endsWith("/skill-version")) switches.push(req.postDataJSON());
  });
  await expect(card.getByRole("textbox", { name: "版本试用选题" })).toHaveCount(0);
  await expect(card.getByRole("combobox", { name: "选择已有选题试用" })).toHaveCount(0);
  await expect(card.getByRole("button", { name: "干净线程试用", exact: true })).toHaveCount(0);
  await expect(card.getByRole("button", { name: "保存版本", exact: true })).toBeEnabled();
  page.once("dialog", dialog => dialog.accept());
  await card.getByRole("button", { name: "保存版本", exact: true }).click();
  await expect(card.getByText("已保存版本", { exact: true })).toBeVisible();
  expect(Object.keys(saves[0]).sort()).toEqual(["expected_digest", "request_id"]);
  expect((await request.get(`/api/series/${series.id}`).then(r => r.json())).mind_skill_id).toBe(fixture.mind.id);
  await card.getByRole("button", { name: "栏目版本", exact: true }).click();
  await card.getByRole("combobox", { name: "版本应用栏目" }).selectOption(series.id);
  page.once("dialog", dialog => dialog.accept());
  await card.getByRole("button", { name: "使用这个版本", exact: true }).click();
  await expect.poll(async () => (await request.get(`/api/series/${series.id}`).then(r => r.json())).mind_skill_id).toBe(candidateId);
  expect(Object.keys(switches[0]).sort()).toEqual(["expected_digest", "expected_revision", "request_id", "skill_id"]);
  expect((await request.get(`/api/series/${other.id}`).then(r => r.json())).mind_skill_id).toBe(fixture.mind.id);
  await card.getByRole("combobox", { name: "查看 Skill 版本" }).selectOption(fixture.mind.id);
  await card.getByRole("button", { name: "栏目版本", exact: true }).click();
  await card.getByRole("combobox", { name: "版本应用栏目" }).selectOption(series.id);
  page.once("dialog", dialog => dialog.accept());
  await card.getByRole("button", { name: "使用这个版本", exact: true }).click();
  await expect.poll(async () => (await request.get(`/api/series/${series.id}`).then(r => r.json())).mind_skill_id).toBe(fixture.mind.id);
  expect(trialPosts).toBe(0); expect(productionPosts).toBe(0);
  await page.screenshot({ path: info.outputPath("simple-versions-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: info.outputPath("simple-versions-mobile.png") });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("unknown switch is checked after reload without dispatching it again", async ({ page, request }) => {
  const fixture = await request.post("/test/skill-files", { data: {} }).then(response => response.json());
  const response = await request.post("/api/series", { data: { name: `直接使用候选-${crypto.randomUUID().slice(0, 8)}`,
    mind_skill_id: fixture.mind.id, production_skill_id: fixture.production.id, description: "", audience: "", request_id: crypto.randomUUID() } });
  expect(response.ok(), await response.text()).toBeTruthy(); const series = (await response.json()).series;
  await page.goto(`/skills?skill=${fixture.mind.id}`);
  const candidateId = await createCandidate(page, page.getByRole("dialog"));
  const payloads: Record<string, unknown>[] = [];
  await page.route(`**/api/series/${series.id}/skill-version`, async route => {
    payloads.push(route.request().postDataJSON());
    const actual = await route.fetch(); expect(actual.ok(), await actual.text()).toBeTruthy();
    if (payloads.length === 1) await route.abort("failed"); else await route.fulfill({ response: actual });
  });
  const card = page.getByRole("dialog");
  await card.getByRole("button", { name: "栏目版本", exact: true }).click();
  await card.getByRole("combobox", { name: "版本应用栏目" }).selectOption(series.id);
  page.once("dialog", dialog => dialog.accept()); await card.getByRole("button", { name: "使用这个版本", exact: true }).click();
  await expect(card.getByRole("button", { name: "核对当前状态" })).toBeVisible();
  const accepted = await request.get(`/api/series/${series.id}`).then(r => r.json()); expect(accepted.mind_skill_id).toBe(candidateId);
  await page.reload();
  await page.getByRole("dialog").getByRole("button", { name: "核对当前状态" }).click();
  await expect(page.getByRole("dialog").getByRole("button", { name: "核对当前状态" })).toHaveCount(0);
  expect(payloads).toHaveLength(1);
  expect((await request.get(`/api/series/${series.id}`).then(r => r.json())).revision).toBe(accepted.revision);
});

test("incomplete Skill write receipt locks the draft across refresh and only reads to verify", async ({ page, request }) => {
  const card = await openMind(page);
  const candidateId = await createCandidate(page, card);
  const history = await request.get(`/api/producer-skills/${candidateId}/versions`).then(response => response.json());
  const candidate = history.items.find((item: { id: string }) => item.id === candidateId);
  expect(candidate?.version_status).toBe("candidate");
  let writes = 0;
  await page.route("**/api/producer-skills/*/files/content*", async route => {
    if (route.request().method() !== "PUT") return route.continue();
    writes++;
    const actual = await route.fetch();
    expect(actual.ok(), await actual.text()).toBeTruthy();
    await route.fulfill({ status: 200, json: {} });
  });
  await card.getByRole("button", { name: "编辑", exact: true }).click();
  const editor = card.getByRole("textbox", { name: "编辑 SKILL.md" });
  await editor.fill(`${await editor.inputValue()}\n已保存但回执不完整。`);
  await card.getByRole("button", { name: "保存文件", exact: true }).click();
  await expect(card.getByText(/上次写入结果待核对/)).toBeVisible();
  await expect(editor).toHaveValue(/已保存但回执不完整/);
  await expect(card.getByRole("button", { name: "保存文件", exact: true })).toBeDisabled();
  await page.reload();
  const reopened = page.getByRole("dialog");
  await expect(reopened.getByText(/刷新前的未保存草稿和待上传图片没有保留/)).toBeVisible();
  await reopened.getByRole("button", { name: "核对写入结果", exact: true }).click();
  await reopened.getByRole("button", { name: `查看 v${candidate.version_number} · 候选`, exact: true }).click();
  await expect(reopened.getByRole("combobox", { name: "查看 Skill 版本" })).toHaveValue(candidateId);
  await expect(reopened.getByText("已保存但回执不完整。", { exact: true })).toBeVisible();
  await expect(reopened.getByText(/不能确认它就是本次写入目标/)).toBeVisible();
  page.once("dialog", confirmation => confirmation.accept());
  await reopened.getByRole("button", { name: "已核对，解除锁定", exact: true }).click();
  expect(writes).toBe(1);
  const saved = await request.get(`/api/producer-skills/${candidateId}/files/text?path=SKILL.md`).then(r => r.json());
  expect(saved.content).toContain("已保存但回执不完整。");
});

test("refresh while a Skill write is in flight keeps a scoped verification marker", async ({ page }) => {
  test.setTimeout(120_000);
  const card = await openMind(page);
  const candidateId = await createCandidate(page, card);
  let accepted!: () => void, release!: () => void;
  const committed = new Promise<void>(resolve => { accepted = resolve; });
  const gate = new Promise<void>(resolve => { release = resolve; });
  let writes = 0;
  await page.route("**/api/producer-skills/*/files/content*", async route => {
    if (route.request().method() !== "PUT") return route.continue();
    writes++;
    const actual = await route.fetch();
    expect(actual.ok(), await actual.text()).toBeTruthy();
    accepted(); await gate;
    try { await route.fulfill({ response: actual }); } catch { /* Reload closed the pending request. */ }
  });
  await card.getByRole("button", { name: "编辑", exact: true }).click();
  const editor = card.getByRole("textbox", { name: "编辑 SKILL.md" });
  await editor.fill(`${await editor.inputValue()}\n请求中刷新验证。`);
  await card.getByRole("button", { name: "保存文件", exact: true }).click();
  await committed;
  const markerKey = `creatoros.skill-write-unknown.v2:global:${encodeURIComponent(candidateId)}`;
  const marker = async () => page.evaluate(key => {
    const value = sessionStorage.getItem(key);
    return value === null ? null : JSON.parse(value);
  }, markerKey);
  expect(await marker()).toEqual({ sourceId: candidateId, path: "SKILL.md", kind: "text" });
  await page.reload(); release();
  expect(await marker()).toEqual({ sourceId: candidateId, path: "SKILL.md", kind: "text" });
  expect(new URL(page.url()).searchParams.get("skill")).toBe(candidateId);
  const reopened = page.getByRole("dialog");
  await expect(reopened).toBeVisible({ timeout: 30_000 });
  await expect(reopened.getByText(/上次写入结果待核对/)).toBeVisible();
  await expect(reopened.getByRole("button", { name: "编辑", exact: true })).toBeDisabled();
  expect(writes).toBe(1);
});

test("a 502 after a real Skill write does not unlock another submission", async ({ page }) => {
  const card = await openMind(page);
  await createCandidate(page, card);
  let writes = 0;
  await page.route("**/api/producer-skills/*/files/content*", async route => {
    if (route.request().method() !== "PUT") return route.continue();
    writes++;
    const actual = await route.fetch();
    expect(actual.ok(), await actual.text()).toBeTruthy();
    await route.fulfill({ status: 502, json: { error: { code: "gateway_failure", message: "网关错误" } } });
  });
  await card.getByRole("button", { name: "编辑", exact: true }).click();
  const editor = card.getByRole("textbox", { name: "编辑 SKILL.md" });
  await editor.fill(`${await editor.inputValue()}\n502 回执验证。`);
  await card.getByRole("button", { name: "保存文件", exact: true }).click();
  await expect(card.getByText(/上次写入结果待核对/)).toBeVisible();
  await expect(card.getByRole("button", { name: "保存文件", exact: true })).toBeDisabled();
  expect(writes).toBe(1);
});

test("a broken JSON receipt after a real Skill write remains unknown", async ({ page }) => {
  const card = await openMind(page);
  await createCandidate(page, card);
  let writes = 0;
  await page.route("**/api/producer-skills/*/files/content*", async route => {
    if (route.request().method() !== "PUT") return route.continue();
    writes++;
    const actual = await route.fetch();
    expect(actual.ok(), await actual.text()).toBeTruthy();
    await route.fulfill({ status: 200, contentType: "application/json", body: "{" });
  });
  await card.getByRole("button", { name: "编辑", exact: true }).click();
  const editor = card.getByRole("textbox", { name: "编辑 SKILL.md" });
  await editor.fill(`${await editor.inputValue()}\n坏 JSON 回执验证。`);
  await card.getByRole("button", { name: "保存文件", exact: true }).click();
  await expect(card.getByText(/上次写入结果待核对/)).toBeVisible();
  await expect(card.getByRole("button", { name: "保存文件", exact: true })).toBeDisabled();
  expect(writes).toBe(1);
});

test("an unreadable 409 receipt after a real Skill write remains unknown", async ({ page, request }) => {
  const card = await openMind(page);
  const candidateId = await createCandidate(page, card);
  let writes = 0;
  await page.route("**/api/producer-skills/*/files/content*", async route => {
    if (route.request().method() !== "PUT") return route.continue();
    writes++;
    const actual = await route.fetch();
    expect(actual.ok(), await actual.text()).toBeTruthy();
    await route.fulfill({ status: 409, contentType: "application/json", body: "{" });
  });
  await card.getByRole("button", { name: "编辑", exact: true }).click();
  const editor = card.getByRole("textbox", { name: "编辑 SKILL.md" });
  await editor.fill(`${await editor.inputValue()}\n不完整 409 回执验证。`);
  await card.getByRole("button", { name: "保存文件", exact: true }).click();
  await expect(card.getByText(/上次写入结果待核对/)).toBeVisible();
  await expect(card.getByRole("button", { name: "保存文件", exact: true })).toBeDisabled();
  expect(writes).toBe(1);
  const saved = await request.get(`/api/producer-skills/${candidateId}/files/text?path=SKILL.md`).then(r => r.json());
  expect(saved.content).toContain("不完整 409 回执验证。");
});

test("an unreadable response stream after a real Skill write remains unknown", async ({ page, request }) => {
  const card = await openMind(page);
  const candidateId = await createCandidate(page, card);
  let writes = 0;
  page.on("request", req => {
    if (req.method() === "PUT" && req.url().includes("/files/content")) writes++;
  });
  await page.evaluate(() => {
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (input, init) => {
      const response = await originalFetch(input, init);
      if (String(input).includes("/files/content") && init?.method === "PUT") {
        response.json = async () => { throw new TypeError("simulated interrupted response stream"); };
      }
      return response;
    };
  });
  await card.getByRole("button", { name: "编辑", exact: true }).click();
  const editor = card.getByRole("textbox", { name: "编辑 SKILL.md" });
  await editor.fill(`${await editor.inputValue()}\n响应流中断验证。`);
  await card.getByRole("button", { name: "保存文件", exact: true }).click();
  await expect(card.getByText(/上次写入结果待核对/)).toBeVisible();
  await expect(card.getByRole("button", { name: "保存文件", exact: true })).toBeDisabled();
  expect(writes).toBe(1);
  const saved = await request.get(`/api/producer-skills/${candidateId}/files/text?path=SKILL.md`).then(r => r.json());
  expect(saved.content).toContain("响应流中断验证。");
});

test("a known unknown-write candidate is read without losing the draft or writing again", async ({ page }) => {
  const card = await openMind(page);
  const candidateId = await createCandidate(page, card);
  let writes = 0;
  await page.route("**/api/producer-skills/*/files/content*", async route => {
    if (route.request().method() !== "PUT") return route.continue();
    writes++;
    const actual = await route.fetch();
    expect(actual.ok(), await actual.text()).toBeTruthy();
    await route.fulfill({ status: 409, json: { error: { code: "studio_outcome_unknown", outcome_unknown: true,
      skill_id: candidateId, path: "SKILL.md", message: "写入结果待核对。" } } });
  });
  await card.getByRole("button", { name: "编辑", exact: true }).click();
  const editor = card.getByRole("textbox", { name: "编辑 SKILL.md" });
  await editor.fill(`${await editor.inputValue()}\n已知候选核对。`);
  await card.getByRole("button", { name: "保存文件", exact: true }).click();
  await expect(card.getByText(/上次写入结果待核对/)).toBeVisible();
  await card.getByRole("button", { name: "核对写入结果", exact: true }).click();
  await expect(card.getByText(/上次写入结果待核对/)).toHaveCount(0);
  await expect(editor).toHaveValue(/已知候选核对/);
  expect(writes).toBe(1);
});

test("account Skill inspector hides another account's candidate and creates its own", async ({ page, request }) => {
  const fixture = await request.post("/test/skill-files", { data: {} }).then(r => r.json());
  const first = await request.post("/api/creators", { data: { display_name: `Skill 账号甲 ${crypto.randomUUID()}` } }).then(r => r.json());
  const second = await request.post("/api/creators", { data: { display_name: `Skill 账号乙 ${crypto.randomUUID()}` } }).then(r => r.json());
  for (const account of [first, second]) {
    const response = await request.post(`/api/creators/${account.id}/skills`, { data: { skill_id: fixture.mind.id, request_id: crypto.randomUUID() } });
    expect(response.ok(), await response.text()).toBeTruthy();
  }
  const foreign = await request.post(`/api/producer-skills/${fixture.mind.id}/versions?creator_id=${second.id}`,
    { data: { expected_digest: fixture.mind.digest, request_id: crypto.randomUUID() } }).then(r => r.json());
  let scopedHistory = false;
  page.on("request", req => {
    const url = new URL(req.url());
    if (url.pathname === `/api/producer-skills/${fixture.mind.id}/versions` && url.searchParams.get("creator_id") === first.id) scopedHistory = true;
  });
  await page.goto(`/?creator=${first.id}&view=skills`);
  await page.getByRole("region", { name: "账号 Skill 库" }).getByRole("button", { name: /inspector-mind/ }).click();
  const card = page.getByRole("dialog", { name: "inspector-mind" });
  await expect(card.getByRole("combobox", { name: "查看 Skill 版本" })).toBeVisible();
  await expect(card.getByRole("combobox", { name: "查看 Skill 版本" }).locator(`option[value="${foreign.id}"]`)).toHaveCount(0);
  const ownId = await createCandidate(page, card);
  expect(scopedHistory).toBe(true);
  const own = await request.get(`/api/producer-skills/${fixture.mind.id}/versions?creator_id=${first.id}`).then(r => r.json());
  expect(own.items.find((item: { id: string }) => item.id === ownId).creator_id).toBe(first.id);
  const otherHistory = await request.get(`/api/producer-skills/${fixture.mind.id}/versions?creator_id=${second.id}`).then(r => r.json());
  expect(otherHistory.items.some((item: { id: string }) => item.id === ownId)).toBe(false);
});

test("a missing unknown candidate can be explicitly unlocked after read-only checking", async ({ page }) => {
  let writes = 0;
  await page.route("**/api/producer-skills/*/versions", async route => {
    if (route.request().method() !== "POST") return route.continue();
    writes++;
    await route.fulfill({ status: 409, json: { error: { code: "studio_outcome_unknown", outcome_unknown: true,
      skill_id: `inspector-mind--${"a".repeat(16)}`, message: "创建结果待核对。" } } });
  });
  const card = await openMind(page);
  await card.getByRole("button", { name: "创建修改候选", exact: true }).click();
  await card.getByRole("button", { name: "核对创建结果", exact: true }).click();
  await expect(card.getByText(/暂未找到回执中的版本/)).toBeVisible();
  page.once("dialog", confirmation => confirmation.accept());
  await card.getByRole("button", { name: "已核对，解除锁定", exact: true }).click();
  await expect(card.getByRole("button", { name: "创建修改候选", exact: true })).toBeEnabled();
  expect(writes).toBe(1);
});

test("library version selection replaces only its Skill query and refresh restores the candidate", async ({ page, request }) => {
  const fixture = await request.post("/test/skill-files", { data: {} }).then(response => response.json());
  await page.goto(`/skills?skill=${fixture.mind.id}&extraction=preserve-existing-task`);
  const candidateId = await createCandidate(page, page.getByRole("dialog"));
  await expect.poll(() => new URL(page.url()).searchParams.get("skill")).toBe(candidateId);
  expect(new URL(page.url()).searchParams.get("extraction")).toBe("preserve-existing-task");
  await page.reload();
  const card = page.getByRole("dialog");
  await expect(card.getByRole("combobox", { name: "查看 Skill 版本" })).toHaveValue(candidateId, { timeout: 20_000 });
  await expect(card.getByRole("button", { name: "保存版本", exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  const box = await card.getByRole("combobox", { name: "查看 Skill 版本" }).boundingBox();
  expect(box!.width).toBeGreaterThanOrEqual(112);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
