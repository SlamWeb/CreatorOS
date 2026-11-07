import { expect, test } from "@playwright/test";
import { limitVisibleSkillCatalog } from "./fixtures/skill-catalog";

// These tests use playwright.config.ts's isolated SQLite and file root. The
// server fixture creates local Skills; no model or production service is used.
test.beforeEach(async ({ request }) => {
  const seeded = await request.post("/test/skill-files", { data: {} });
  expect(seeded.ok(), await seeded.text()).toBeTruthy();
});

test("Skill creation and GitHub install actions share a row and the install form remains usable", async ({ page }) => {
  await page.goto("/skills");
  const extract = page.getByRole("button", { name: "从作品提炼" });
  const author = page.getByRole("button", { name: "自己写 Skill" });
  const install = page.getByRole("button", { name: "从 GitHub 安装" });
  await expect(install).toBeVisible({ timeout: 15000 });
  const positions = await Promise.all([extract.boundingBox(), author.boundingBox(), install.boundingBox()]);
  expect(positions.every(box => box && Math.abs(box.y - positions[0]!.y) < 2)).toBeTruthy();
  await install.click();
  await expect(install).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("textbox", { name: "GitHub Skill 链接" })).toBeVisible();
  await install.click();
  await expect(page.getByRole("textbox", { name: "GitHub Skill 链接" })).toBeHidden();
});

test("search and pools preserve the selected Skill across refresh", async ({ page }, info) => {
  await limitVisibleSkillCatalog(page, ["inspector-mind", "inspector-visual", "knowledge-to-carousel"]);
  const writes: string[] = [];
  page.on("request", request => {
    if (["POST", "PUT", "PATCH", "DELETE"].includes(request.method())) writes.push(new URL(request.url()).pathname);
  });
  await page.goto("/skills");
  const search = page.getByRole("searchbox", { name: "搜索 Skill" });
  const basket = page.getByLabel("Skill 组合框", { exact: true });
  const mind = page.getByRole("button", { name: "inspector-mind", exact: true });
  const visual = page.getByRole("button", { name: "inspector-visual", exact: true });

  await expect(mind).toBeVisible({ timeout: 15000 });
  await expect(visual).toBeVisible();
  await mind.click();
  const detail = page.getByRole("dialog", { name: "inspector-mind" });
  await expect(detail).toBeVisible();
  await detail.getByRole("button", { name: "加入组合", exact: true }).click();
  await expect(detail).toBeHidden();
  await expect(basket).toContainText("inspector-mind");

  await expect(page.getByRole("region", { name: "内容创作池" })).toBeVisible();
  await expect(page.getByRole("region", { name: "待分类池" })).toContainText("inspector-mind");
  await search.fill("inspector-visual");
  await expect(visual).toBeVisible();
  await search.fill("不存在的关键词");
  await expect(visual).toHaveCount(0);
  await expect(basket).toContainText("inspector-mind");
  await page.screenshot({ path: info.outputPath("skill-library-no-results-desktop.png"), fullPage: true });
  await page.reload();
  await expect(search).toHaveValue("不存在的关键词");
  await expect(basket).toContainText("inspector-mind");

  await search.clear();
  await expect(mind).toBeVisible();
  await page.reload();
  await expect(basket).toContainText("inspector-mind");
  await expect(mind).toBeVisible();
  await page.screenshot({ path: info.outputPath("skill-library-selected-desktop.png"), fullPage: true });

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(search).toBeVisible();
  await visual.click();
  const mobileDetail = page.getByRole("dialog", { name: "inspector-visual" });
  await expect(mobileDetail).toBeVisible();
  await page.screenshot({ path: info.outputPath("skill-library-detail-mobile.png"), fullPage: true });
  await mobileDetail.getByRole("button", { name: "加入组合", exact: true }).click();
  await expect(basket).toContainText("inspector-visual");
  await expect(basket).toContainText("inspector-mind");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("skill-library-selected-mobile.png"), fullPage: true });
  expect(writes).toEqual([]);
});

test("a dense catalog remains readable on desktop and mobile", async ({ page }, info) => {
  // Controlled read projection, based on a real isolated Skill response. These
  // extra rows are never opened or written and are not evidence of live data.
  await page.route("**/api/producer-skills", async route => {
    const response = await route.fetch();
    const result = await response.json();
    const visual = result.items.find((item: { name: string }) => item.name === "inspector-visual");
    expect(visual).toBeTruthy();
    result.items = Array.from({ length: 24 }, (_, index) => ({
      ...visual,
      id: `layout-only-${index}`,
      name: `演示 Skill ${index + 1}`,
      display_name: index === 0 ? "很长的 Skill 名称用于检查中文换行和英文 overflow example" : `演示 Skill ${index + 1}`,
    }));
    await route.fulfill({ response, json: result });
  });
  await page.goto("/skills");
  await expect(page.getByRole("button", { name: "很长的 Skill 名称用于检查中文换行和英文 overflow example", exact: true })).toBeVisible();
  const pool = page.getByRole("region", { name: "待分类池" });
  const toggle = pool.getByRole("button", { name: "展开待分类池" });
  await expect(pool.getByRole("button", { name: "演示 Skill 6", exact: true })).toHaveCount(0);
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await page.screenshot({ path: info.outputPath("skill-library-dense-desktop.png"), fullPage: true });
  await toggle.click();
  await expect(pool.getByRole("button", { name: "演示 Skill 6", exact: true })).toBeVisible();
  await pool.getByRole("button", { name: "收起待分类池" }).click();
  await expect(pool.getByRole("button", { name: "演示 Skill 6", exact: true })).toHaveCount(0);
  await page.getByRole("searchbox", { name: "搜索 Skill" }).fill("演示 Skill 24");
  await expect(pool.getByRole("button", { name: "演示 Skill 24", exact: true })).toBeVisible();
  await page.getByRole("searchbox", { name: "搜索 Skill" }).clear();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("searchbox", { name: "搜索 Skill" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("skill-library-dense-mobile.png"), fullPage: true });
});

test("dragging an unclassified Skill into a pool persists its category", async ({ page, request }) => {
  await limitVisibleSkillCatalog(page, ["inspector-mind", "inspector-visual", "knowledge-to-carousel"]);
  await page.goto("/skills");
  const source = page.getByRole("region", { name: "待分类池" }).getByRole("button", { name: "knowledge-to-carousel", exact: true });
  const target = page.getByRole("region", { name: "内容创作池" });
  await expect(source).toBeVisible({ timeout: 15000 });
  await source.dragTo(target);
  await expect(target.getByRole("button", { name: "knowledge-to-carousel", exact: true })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "已将" })).toBeVisible();
  const response = await request.get("/api/skill-display-categories");
  const data = await response.json();
  expect(data.items.find((item: { family_id: string }) => item.family_id === "knowledge-to-carousel").categories).toEqual(["content"]);
  await page.reload();
  await expect(target.getByRole("button", { name: "knowledge-to-carousel", exact: true })).toBeVisible();
});

test("a failed classification drop preserves the intended category for review", async ({ page }) => {
  await limitVisibleSkillCatalog(page, ["inspector-mind", "inspector-visual", "knowledge-to-carousel"]);
  await page.route("**/api/skill-display-categories/*", route => route.fulfill({ status: 409, json: { detail: "受控冲突" } }));
  await page.goto("/skills");
  const source = page.getByRole("region", { name: "待分类池" }).getByRole("button", { name: "inspector-visual", exact: true });
  await expect(source).toBeVisible({ timeout: 15000 });
  await source.dragTo(page.getByRole("region", { name: "视觉表现池" }));
  const editor = page.getByRole("dialog", { name: "设置 Skill 分类" });
  await expect(editor.getByLabel("视觉表现")).toBeChecked();
  await expect(editor.getByRole("button", { name: "保存分类" })).toBeDisabled();
  await expect(editor.getByRole("button", { name: "核对最新分类" })).toBeVisible();
});

test("category edits persist and multi-category selection stays synchronized", async ({ page }, info) => {
  await limitVisibleSkillCatalog(page, ["inspector-mind", "inspector-visual", "knowledge-to-carousel"]);
  await page.goto("/skills");
  await page.getByRole("button", { name: "设置 inspector-mind 分类" }).click();
  const editor = page.getByRole("dialog", { name: "设置 Skill 分类" });
  await editor.getByLabel("内容创作").check();
  await editor.getByLabel("规则约束").check();
  await editor.getByRole("button", { name: "保存分类" }).click();
  await expect(editor).toBeHidden();
  await page.reload();
  const content = page.getByRole("region", { name: "内容创作池" });
  const rules = page.getByRole("region", { name: "规则约束池" });
  await expect(content.getByRole("button", { name: "inspector-mind", exact: true })).toBeVisible();
  await expect(rules.getByRole("button", { name: "inspector-mind", exact: true })).toBeVisible();
  await expect(content.getByRole("button", { name: "设置 inspector-mind 分类" })).toHaveAttribute("aria-description", "内容创作、规则约束");
  await content.getByRole("button", { name: "加入 inspector-mind" }).click();
  await expect(rules.getByRole("button", { name: "取消选择 inspector-mind" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("region", { name: "待分类池" })).toContainText("inspector-visual");
  await page.screenshot({ path: info.outputPath("skill-pools-desktop.png"), fullPage: true });
});

test("category pools expand independently without changing the selection", async ({ page }) => {
  await page.route("**/api/producer-skills", async route => {
    const response = await route.fetch();
    const result = await response.json();
    const sample = result.items.find((item: { name: string }) => item.name === "inspector-visual");
    result.items = Array.from({ length: 13 }, (_, index) => ({ ...sample,
      id: `pool-fixture-${index}`, family_id: `pool-fixture-${index}`,
      name: `池子测试 ${index + 1}`, display_name: `池子测试 ${index + 1}` }));
    await route.fulfill({ response, json: result });
  });
  await page.route("**/api/skill-display-categories", route => route.fulfill({ json: {
    items: Array.from({ length: 13 }, (_, index) => ({ family_id: `pool-fixture-${index}`,
      categories: [index < 7 ? "content" : "visual"], revision: 1 }))
  } }));
  await page.goto("/skills");
  const content = page.getByRole("region", { name: "内容创作池" });
  const visual = page.getByRole("region", { name: "视觉表现池" });
  await expect(content.getByRole("heading", { name: "内容创作", exact: true })).toBeVisible();
  await expect(content.getByRole("button", { name: "池子测试 6", exact: true })).toHaveCount(0);
  await expect(visual.getByRole("button", { name: "池子测试 13", exact: true })).toHaveCount(0);
  await content.getByRole("button", { name: "加入 池子测试 1" }).click();
  await content.getByRole("button", { name: "展开内容创作池" }).click();
  await expect(content.getByRole("button", { name: "池子测试 6", exact: true })).toBeVisible();
  await expect(visual.getByRole("button", { name: "池子测试 13", exact: true })).toHaveCount(0);
  await content.getByRole("button", { name: "收起内容创作池" }).click();
  await expect(page.getByLabel("Skill 组合框", { exact: true })).toContainText("池子测试 1");
  await expect(content.getByRole("button", { name: "取消选择 池子测试 1" })).toHaveAttribute("aria-pressed", "true");
});

test("a pending merge cannot change the selected Skills via drag or add", async ({ page }) => {
  await limitVisibleSkillCatalog(page, ["inspector-mind", "inspector-visual", "knowledge-to-carousel"]);
  let release!: () => void;
  let started!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const requested = new Promise<void>(resolve => { started = resolve; });
  await page.route("**/api/skill-extractions/merge", async route => {
    started();
    await gate;
    await route.fulfill({ status: 503, json: { detail: "受控的融合失败" } });
  });
  try {
    await page.goto("/skills");
    await page.getByRole("button", { name: "加入 inspector-mind", exact: true }).first().click();
    await page.getByRole("button", { name: "加入 inspector-visual", exact: true }).click();
    await page.getByRole("radio", { name: "融合成新 Skill" }).check();
    await page.getByRole("button", { name: "生成融合草稿" }).click();
    await requested;
    const add = page.getByRole("button", { name: "加入 knowledge-to-carousel", exact: true });
    await expect(add).toBeDisabled();
    const source = page.getByRole("button", { name: "knowledge-to-carousel", exact: true });
    const basket = page.getByLabel("Skill 组合框", { exact: true });
    await source.dragTo(basket);
    await expect(basket).not.toContainText("knowledge-to-carousel");
    await expect(basket).toContainText("inspector-mind");
    await expect(basket).toContainText("inspector-visual");
  } finally { release(); }
});
