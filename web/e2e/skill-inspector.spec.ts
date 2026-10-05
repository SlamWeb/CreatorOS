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
  await expect(page.getByRole("button", { name: "Mind 槽", exact: true })).not.toContainText("inspector-mind");
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
  const slot = page.getByRole("button", { name: "Mind 槽", exact: true });
  await expect(slot).toContainText("inspector-mind");
  await page.getByRole("button", { name: "查看 Mind Skill", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "关闭 Skill 详情", exact: true }).click();
  await expect(slot).toContainText("inspector-mind");
  await slot.click();
  await expect(slot).not.toContainText("inspector-mind");
  await page.getByRole("button", { name: "knowledge-to-carousel", exact: true }).click();
  await expect(page.getByRole("dialog").getByRole("heading", { name: "knowledge-to-carousel" })).toBeVisible();
  await expect(page.getByRole("dialog").getByRole("button", { name: "加入组合" })).toHaveCount(0);
});

test("drag still selects the matching slot without opening inspector", async ({ page }) => {
  await page.goto("/skills");
  const card = page.getByRole("button", { name: "inspector-visual", exact: true });
  const slot = page.getByRole("button", { name: "Visualize 槽", exact: true });
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
