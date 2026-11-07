import { expect, test } from "@playwright/test";
import { limitVisibleSkillCatalog } from "./fixtures/skill-catalog";

// These tests use playwright.config.ts's isolated SQLite and file root. The
// server fixture creates local Skills; no model or production service is used.
test.beforeEach(async ({ request }) => {
  const seeded = await request.post("/test/skill-files", { data: {} });
  expect(seeded.ok(), await seeded.text()).toBeTruthy();
});

test("search and category filters preserve the selected Skill across refresh", async ({ page }, info) => {
  await limitVisibleSkillCatalog(page, ["inspector-mind", "inspector-visual", "knowledge-to-carousel"]);
  const writes: string[] = [];
  page.on("request", request => {
    if (["POST", "PUT", "PATCH", "DELETE"].includes(request.method())) writes.push(new URL(request.url()).pathname);
  });
  await page.goto("/skills");
  const search = page.getByRole("searchbox", { name: "搜索 Skill" });
  const classification = page.getByRole("group", { name: "Skill 分类" });
  const basket = page.getByLabel("Skill 组合框", { exact: true });
  const mind = page.getByRole("button", { name: "inspector-mind", exact: true });
  const visual = page.getByRole("button", { name: "inspector-visual", exact: true });

  await expect(mind).toBeVisible();
  await expect(visual).toBeVisible();
  await mind.click();
  const detail = page.getByRole("dialog", { name: "inspector-mind" });
  await expect(detail).toBeVisible();
  await detail.getByRole("button", { name: "加入组合", exact: true }).click();
  await expect(detail).toBeHidden();
  await expect(basket).toContainText("inspector-mind");

  await classification.getByRole("button", { name: "内容创作" }).click();
  await expect(visual).toHaveCount(0);
  await expect(mind).toHaveCount(0);
  await expect(basket).toContainText("inspector-mind");
  await classification.getByRole("button", { name: "全部", exact: true }).click();
  await search.fill("inspector-visual");
  await expect(visual).toBeVisible();
  await search.fill("不存在的关键词");
  await expect(visual).toHaveCount(0);
  await expect(basket).toContainText("inspector-mind");
  await page.screenshot({ path: info.outputPath("skill-library-no-results-desktop.png"), fullPage: true });
  await page.reload();
  await expect(search).toHaveValue("不存在的关键词");
  await expect(search).toHaveValue("不存在的关键词");
  await expect(basket).toContainText("inspector-mind");

  await search.clear();
  await classification.getByRole("button", { name: "全部", exact: true }).click();
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
  await page.screenshot({ path: info.outputPath("skill-library-dense-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("searchbox", { name: "搜索 Skill" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("skill-library-dense-mobile.png"), fullPage: true });
});

test("category edits persist and support multiple filters", async ({ page }) => {
  await limitVisibleSkillCatalog(page, ["inspector-mind", "inspector-visual", "knowledge-to-carousel"]);
  await page.goto("/skills");
  await page.getByRole("button", { name: "设置 inspector-mind 分类" }).click();
  const editor = page.getByRole("dialog", { name: "设置 Skill 分类" });
  await editor.getByLabel("内容创作").check();
  await editor.getByLabel("规则约束").check();
  await editor.getByRole("button", { name: "保存分类" }).click();
  await expect(editor).toBeHidden();
  await page.reload();
  const categoryButton = page.getByRole("button", { name: "设置 inspector-mind 分类" });
  await expect(categoryButton).toHaveText("内容创作 +1");
  await expect(categoryButton).toHaveAttribute("aria-description", "内容创作、规则约束");
  const filters = page.getByRole("group", { name: "Skill 分类" });
  await filters.getByRole("button", { name: "规则约束" }).click();
  await expect(page.getByRole("button", { name: "inspector-mind", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "inspector-visual", exact: true })).toHaveCount(0);
  await filters.getByRole("button", { name: "内容创作" }).click();
  await expect(page.getByRole("button", { name: "inspector-mind", exact: true })).toBeVisible();
  await filters.getByRole("button", { name: "待分类" }).click();
  await expect(page.getByRole("button", { name: "inspector-mind", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "inspector-visual", exact: true })).toBeVisible();
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
    await page.getByRole("button", { name: "加入 inspector-mind", exact: true }).click();
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
