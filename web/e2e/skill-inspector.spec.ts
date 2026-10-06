import { expect, test, type Page } from "@playwright/test";

async function openMind(page: Page) {
  await page.goto("/skills");
  const card = page.getByRole("button", { name: "inspector-mind", exact: true });
  await card.click();
  const dialog = page.getByRole("dialog", { name: "inspector-mind" });
  await expect(dialog.getByText("完整 Skill 结尾标记", { exact: true })).toBeAttached();
  return dialog;
}

test.beforeEach(async ({ request }) => {
  const result = await request.post("/test/skill-files", { data: {} });
  expect(result.ok(), await result.text()).toBeTruthy();
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
  await expect(dialog.getByText(/此文件不支持预览/)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: "inspector-mind", exact: true })).toBeFocused();
  expect(writes).toEqual([]);
  expect(external).toEqual([]);
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
  await dialog.locator(".skill-inspector-sidebar > summary").click();
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
  let fail = true;
  await page.route("**/api/producer-skills/*/files", async route => {
    if (fail) { fail = false; await route.fulfill({ status: 503, json: { error: { message: "临时读取失败" } } }); }
    else await route.continue();
  });
  await page.goto("/skills");
  await page.getByRole("button", { name: "inspector-mind", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("alert")).toContainText("临时读取失败");
  await dialog.getByRole("button", { name: "重新读取", exact: true }).click();
  await expect(dialog.getByText("完整 Skill 结尾标记", { exact: true })).toBeAttached();
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
  await dialog.getByRole("button", { name: "覆盖保存", exact: true }).click();
  await expect(dialog.getByText("冲突草稿仍在。", { exact: true })).toBeVisible();
  expect(saveDigests[1]).toBe(currentTextDigest);
});

test("Skill change request enters the existing Agent composer as a draft without sending", async ({ page }) => {
  const writes: string[] = [];
  page.on("request", req => { if (req.url().includes("/api/agent/sessions") && req.method() !== "GET") writes.push(req.url()); });
  const dialog = await openMind(page);
  await dialog.getByRole("button", { name: "让 Agent 帮我修改当前 Skill", exact: true }).click();
  await dialog.getByRole("textbox", { name: "让 Agent 修改 inspector-mind" }).fill("保留现有结构，把目标读者调整为刚入门的创作者。");
  await dialog.getByRole("button", { name: "在 Agent 中继续", exact: true }).click();
  await expect(page).toHaveURL(/\/agent/);
  const composer = page.getByRole("textbox", { name: "给 Agent 的消息" });
  await expect(composer).toHaveValue(/inspector-mind[\s\S]*保留现有结构/);
  await expect(composer).toHaveValue(/目标文件：SKILL\.md/);
  expect(writes).toEqual([]);
});

test("Run detail shows its frozen Skill and opens the current local card", async ({ page, request }, info) => {
  const seeded = await request.post("/test/skill-files", { data: {} });
  expect(seeded.ok(), await seeded.text()).toBeTruthy();
  const fixture = await seeded.json() as { mind: { id: string }; production: { id: string } };
  const snapshot = { creator_id: "e2e_lab", series_id: "e2e_series", topic_id: "e2e_topic",
    topic_title: "冻结 Skill 版本验收", skill_name: fixture.mind.id, skill_digest: "a".repeat(64) };
  await page.route("**/api/runs/run-skill-fixture", route => route.fulfill({ json: {
    id: "run-skill-fixture", creator_id: "e2e_lab", creator_name: "隔离账号", series_id: "e2e_series", series_name: "Skill 来源验收",
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
  await expect(used).toContainText("aaaaaaaaaaaa…");
  await expect(used).toContainText("编辑只影响新 Run");
  await expect(used.getByRole("button", { name: "打开当前库版本 ↗" })).toBeVisible();
  await page.screenshot({ path: info.outputPath("run-skill-desktop.png"), fullPage: true });
  await used.getByRole("button", { name: "打开当前库版本 ↗" }).click();
  const card = page.getByRole("dialog", { name: "inspector-mind" });
  await expect(card).toBeVisible();
  await expect(card.getByRole("button", { name: "编辑", exact: true })).toBeVisible();
  await card.getByRole("button", { name: "关闭 Skill 详情" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: info.outputPath("run-skill-mobile.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});
