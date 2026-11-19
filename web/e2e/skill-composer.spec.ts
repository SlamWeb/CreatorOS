import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { limitVisibleSkillCatalog } from "./fixtures/skill-catalog";

test.beforeEach(async ({ request }) => {
  const seeded = await request.post("/test/skill-files", { data: {} });
  expect(seeded.ok(), await seeded.text()).toBeTruthy();
});

async function dragSkill(page: Page, name: string) {
  const card = page.getByRole("button", { name, exact: true });
  const basket = page.getByLabel("已选 Skill", { exact: true });
  const start = await card.boundingBox();
  const finish = await basket.boundingBox();
  expect(start && finish).toBeTruthy();
  await page.mouse.move(start!.x + start!.width / 2, start!.y + start!.height / 2);
  await page.mouse.down();
  await page.mouse.move(finish!.x + finish!.width / 2, finish!.y + finish!.height / 2, { steps: 12 });
  await page.mouse.up();
  await expect(basket).toContainText(name);
}

async function addSkill(page: Page, name: string) {
  await page.getByRole("searchbox", { name: "搜索 Skill" }).fill(name);
  await page.getByRole("button", { name, exact: true }).click();
  await page.getByRole("dialog", { name }).getByRole("button", { name: "选入栏目", exact: true }).click();
}

test("dragging a Skill near the viewport edge scrolls a long list to the basket", async ({ page }) => {
  // The long catalog is a controlled read projection; the dragged Skill is real.
  await page.route("**/api/producer-skills", async route => {
    const response = await route.fetch();
    const result = await response.json();
    const visual = result.items.find((item: { name: string }) => item.name === "inspector-visual");
    expect(visual).toBeTruthy();
    result.items = [...Array.from({ length: 60 }, (_, i) => ({ ...visual, id: `filler-${i}`, name: `测试占位 ${i}`, display_name: `测试占位 ${i}` })), visual];
    await route.fulfill({ response, json: result });
  });
  await page.goto("/skills");
  await page.getByRole("button", { name: "展开待分类池" }).click();
  const card = page.getByRole("button", { name: "inspector-visual", exact: true });
  await card.scrollIntoViewIfNeeded();
  const start = await card.boundingBox();
  expect(start).toBeTruthy();
  const initialScroll = await page.evaluate(() => window.scrollY);
  expect(initialScroll).toBeGreaterThan(200);
  await page.mouse.move(start!.x + start!.width / 2, start!.y + start!.height / 2);
  await page.mouse.down();
  await page.mouse.move(start!.x + start!.width / 2, 5, { steps: 12 });
  await expect.poll(() => page.evaluate(() => window.scrollY), { timeout: 8000 }).toBeLessThan(100);
  const basket = page.getByLabel("已选 Skill", { exact: true });
  const finish = await basket.boundingBox();
  expect(finish).toBeTruthy();
  await page.mouse.move(finish!.x + finish!.width / 2, finish!.y + finish!.height / 2);
  await page.mouse.up();
  await expect(basket).toContainText("inspector-visual");
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("free composer accepts different Skill roles, deduplicates, removes and restores session draft", async ({ page }) => {
  await limitVisibleSkillCatalog(page, ["inspector-mind", "inspector-visual", "knowledge-to-carousel"]);
  await page.goto("/skills");
  const basket = page.getByLabel("已选 Skill", { exact: true });
  await dragSkill(page, "inspector-mind");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("button", { name: "inspector-mind", exact: true }).click();
  await page.getByRole("dialog", { name: "inspector-mind" })
    .getByRole("button", { name: "选入栏目", exact: true }).click();
  await expect(page.getByRole("button", { name: "移除 inspector-mind", exact: true })).toHaveCount(1);

  await page.getByRole("button", { name: "inspector-visual", exact: true }).click();
  await page.getByRole("dialog", { name: "inspector-visual" })
    .getByRole("button", { name: "选入栏目", exact: true }).click();
  await expect(basket).toContainText("inspector-visual");
  await page.getByRole("button", { name: "移除 inspector-visual", exact: true }).click();
  await expect(basket).not.toContainText("inspector-visual");
  await dragSkill(page, "inspector-visual");

  await page.getByLabel("栏目名称", { exact: true }).fill("刷新保留的组合草稿");
  await page.reload();
  await expect(basket).toContainText("inspector-mind");
  await expect(basket).toContainText("inspector-visual");
  await expect(page.getByLabel("栏目名称", { exact: true })).toHaveValue("刷新保留的组合草稿");
  await expect(page.getByLabel("栏目定位", { exact: true })).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});

async function createCreator(request: APIRequestContext) {
  const response = await request.post("/api/creators", { data: { display_name: "素材账号-" + crypto.randomUUID().slice(0, 8) } });
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}

test("creates a series and both Skill libraries from three original Skills in one write", async ({ page, request }, testInfo) => {
  const creator = await createCreator(request);
  const before = await request.get("/test/composer-evidence").then(reply => reply.json());
  const catalog = await request.get("/api/producer-skills").then(reply => reply.json());
  const names = ["inspector-mind", "inspector-visual", "knowledge-to-carousel"];
  const ids = names.map(name => catalog.items.find((item: { name: string }) => item.name === name).id);
  const extraWrites: string[] = [];
  page.on("request", req => {
    if (req.method() === "POST" && new URL(req.url()).pathname !== "/api/series") extraWrites.push(req.url());
  });
  await page.goto("/skills");
  await expect(page.getByRole("heading", { name: "Skill Libraries", exact: true })).toBeVisible();
  for (const skill of names) await addSkill(page, skill);
  const name = "三个原始 Skill-" + crypto.randomUUID().slice(0, 8);
  await page.getByLabel("栏目名称", { exact: true }).fill(name);
  const submit = page.getByRole("button", { name: "创建栏目", exact: true });
  await expect(submit).toBeDisabled();
  await expect(page.getByRole("radio", { name: "融合成新 Skill", exact: true })).toHaveCount(0);
  await page.getByLabel("归属账号").selectOption(creator.id);
  await expect(submit).toBeEnabled();
  await page.screenshot({ path: testInfo.outputPath("skill-libraries-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await submit.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("skill-libraries-mobile.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  const completion = page.waitForResponse(reply => reply.request().method() === "POST"
    && new URL(reply.url()).pathname === "/api/series");
  await submit.click();
  const response = await completion;
  expect(response.status(), await response.text()).toBe(201);
  expect(response.request().postDataJSON()).toMatchObject({ name, creator_id: creator.id, skill_ids: ids });
  expect(response.request().postDataJSON()).not.toHaveProperty("skill_name");
  const { series } = await response.json();
  expect(series).toMatchObject({ name, skill_name: null, mind_skill_id: null, production_skill_id: null });
  await page.getByRole("link", { name: "打开栏目" }).click();
  await expect(page).toHaveURL(new RegExp("series=" + series.id));
  await page.reload();
  await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
  for (const url of ["/api/series/" + series.id + "/skills", "/api/creators/" + creator.id + "/skills"]) {
    const view = await request.get(url).then(reply => reply.json());
    expect(view.items.map((item: { id: string }) => item.id)).toEqual(ids);
  }
  const after = await request.get("/test/composer-evidence?name=" + encodeURIComponent(name)).then(reply => reply.json());
  expect(after.series).toHaveLength(1);
  expect(after.counts).toEqual(before.counts);
  expect(after.execution_counts).toEqual(before.execution_counts);
  expect(after.extraction_jobs).toBe(before.extraction_jobs);
  expect(after.files).toEqual(before.files);
  expect(extraWrites).toEqual([]);
});

test("a failed create keeps the selection and does not create a second series", async ({ page, request }) => {
  const creator = await createCreator(request);
  await page.goto("/skills");
  await page.getByLabel("归属账号").selectOption(creator.id);
  await addSkill(page, "inspector-mind");
  const name = "单 Skill 栏目-" + crypto.randomUUID().slice(0, 8);
  const title = page.getByLabel("栏目名称", { exact: true });
  const submit = page.getByRole("button", { name: "创建栏目", exact: true });
  await title.fill(name);
  await submit.click();
  await expect(page.getByRole("link", { name: "打开栏目" })).toBeVisible();
  await addSkill(page, "inspector-visual");
  await page.getByLabel("归属账号").selectOption(creator.id);
  await title.fill(name);
  await submit.click();
  await expect(page.getByRole("alert").filter({ hasText: "同名栏目" })).toBeVisible();
  await expect(page.getByLabel("已选 Skill")).toContainText("inspector-visual");
  const account = await request.get("/api/creators/" + creator.id + "/skills").then(reply => reply.json());
  expect(account.items.map((item: { name: string }) => item.name)).toEqual(["inspector-mind"]);
});

test("unknown direct-create response retries the same receipt after reload instead of creating twice", async ({ page, request }) => {
  const creator = await createCreator(request);
  const name = `响应丢失只建一次-${crypto.randomUUID().slice(0, 8)}`;
  const sent: Array<Record<string, unknown>> = [];
  await page.route("**/api/series", async route => {
    if (route.request().method() !== "POST") return route.continue();
    sent.push(route.request().postDataJSON());
    // The write really reaches SQLite; only delivery of its first response fails.
    const response = await route.fetch();
    expect(response.status()).toBe(201);
    if (sent.length === 1) await route.abort("failed");
    else await route.fulfill({ response });
  });
  await page.goto("/skills");
  await addSkill(page, "inspector-mind");
  await addSkill(page, "inspector-visual");
  await page.getByLabel("归属账号").selectOption(creator.id);
  await page.getByLabel("栏目名称", { exact: true }).fill(name);
  await page.getByRole("button", { name: "创建栏目", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  const first = await request.get(`/test/composer-evidence?name=${encodeURIComponent(name)}`).then(reply => reply.json());
  expect(first.series).toHaveLength(1);
  await page.reload();
  await expect(page.getByLabel("栏目名称", { exact: true })).toHaveValue(name);
  const retry = page.waitForResponse(reply => reply.request().method() === "POST"
    && new URL(reply.url()).pathname === "/api/series");
  await page.getByRole("button", { name: "创建栏目", exact: true }).click();
  const result = await (await retry).json();
  expect(result.deduplicated).toBe(true);
  expect(sent).toHaveLength(2);
  expect(sent[1]).toEqual(sent[0]);
  await expect(page.getByRole("status").filter({ hasText: `已创建「${name}」。` })).toContainText(`已创建「${name}」。`);
  const after = await request.get(`/test/composer-evidence?name=${encodeURIComponent(name)}`).then(reply => reply.json());
  expect(after.series).toEqual(first.series);
  expect(after.counts).toEqual(first.counts);
  expect(after.execution_counts).toEqual(first.execution_counts);
  expect(after.extraction_jobs).toBe(first.extraction_jobs);
  expect(after.files).toEqual(first.files);
});
