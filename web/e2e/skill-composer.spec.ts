import { expect, test, type Page } from "@playwright/test";

const tinyPng = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/pC8AAAAASUVORK5CYII=", "base64");

test.beforeEach(async ({ request }) => {
  const seeded = await request.post("/test/skill-files", { data: {} });
  expect(seeded.ok(), await seeded.text()).toBeTruthy();
});

async function dragSkill(page: Page, name: string) {
  const card = page.getByRole("button", { name, exact: true });
  const basket = page.getByLabel("Skill 组合框", { exact: true });
  const start = await card.boundingBox();
  const finish = await basket.boundingBox();
  expect(start && finish).toBeTruthy();
  await page.mouse.move(start!.x + start!.width / 2, start!.y + start!.height / 2);
  await page.mouse.down();
  await page.mouse.move(finish!.x + finish!.width / 2, finish!.y + finish!.height / 2, { steps: 12 });
  await page.mouse.up();
  await expect(basket).toContainText(name);
}

test("free composer accepts different Skill roles, deduplicates, removes and restores session draft", async ({ page }) => {
  await page.goto("/skills");
  const basket = page.getByLabel("Skill 组合框", { exact: true });

  await dragSkill(page, "inspector-mind");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("button", { name: "inspector-mind", exact: true }).click();
  await page.getByRole("dialog", { name: "inspector-mind" })
    .getByRole("button", { name: "加入组合", exact: true }).click();
  await expect(page.getByRole("button", { name: "移除 inspector-mind", exact: true })).toHaveCount(1);

  await page.getByRole("button", { name: "inspector-visual", exact: true }).click();
  await page.getByRole("dialog", { name: "inspector-visual" })
    .getByRole("button", { name: "加入组合", exact: true }).click();
  await expect(basket).toContainText("inspector-visual");
  await page.getByRole("button", { name: "移除 inspector-visual", exact: true }).click();
  await expect(basket).not.toContainText("inspector-visual");
  await dragSkill(page, "inspector-visual");

  await page.getByLabel("栏目名称", { exact: true }).fill("刷新保留的组合草稿");
  await page.getByLabel("栏目定位", { exact: true }).fill("仅用于隔离 E2E");
  await page.reload();
  await expect(basket).toContainText("inspector-mind");
  await expect(basket).toContainText("inspector-visual");
  await expect(page.getByLabel("栏目名称", { exact: true })).toHaveValue("刷新保留的组合草稿");
  await expect(page.getByLabel("栏目定位", { exact: true })).toHaveValue("仅用于隔离 E2E");

  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});

test("builtin Skill creates a real single-Skill series through the isolated API", async ({ page, request }) => {
  await page.goto("/skills");
  const builtin = page.getByRole("button", { name: "knowledge-to-carousel", exact: true });
  await builtin.click();
  await page.getByRole("dialog", { name: "knowledge-to-carousel" })
    .getByRole("button", { name: "加入组合", exact: true }).click();
  await page.getByLabel("栏目名称", { exact: true }).fill(`真实单 Skill 栏目-${crypto.randomUUID().slice(0, 8)}`);

  const createRequest = page.waitForRequest(event => event.method() === "POST" && new URL(event.url()).pathname === "/api/series");
  const createResponse = page.waitForResponse(response => response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/series");
  await page.getByRole("button", { name: "创建栏目", exact: true }).click();
  const sent = await createRequest;
  const payload = sent.postDataJSON();
  expect(payload).toMatchObject({ skill_name: "knowledge-to-carousel" });
  expect(payload).not.toHaveProperty("mind_skill_id");
  expect(payload).not.toHaveProperty("production_skill_id");
  const response = await createResponse;
  expect(response.status(), await response.text()).toBe(201);
  const result = await response.json();
  const persisted = await request.get("/api/series").then(reply => reply.json());
  expect(persisted).toEqual(expect.arrayContaining([
    expect.objectContaining({ id: result.series.id, skill_name: "knowledge-to-carousel", is_active: true }),
  ]));
});

test("controlled native merge stays a draft until saved, then explicit single-Skill composition", async ({ page, request }) => {
  const catalogResponse = await request.get("/api/producer-skills");
  expect(catalogResponse.ok(), await catalogResponse.text()).toBeTruthy();
  const catalog = (await catalogResponse.json()).items as Array<Record<string, unknown>>;
  const mind = catalog.find(item => item.name === "inspector-mind")!;
  const visual = catalog.find(item => item.name === "inspector-visual")!;
  let mergeCount = 0;
  let draftSaveCount = 0;
  let saveCount = 0;
  let seriesWriteCount = 0;
  // The extraction/save response is controlled; compose uses the real builtin
  // ID so the follow-on Series request can exercise the actual API safely.
  const savedSkill = catalog.find(item => item.id === "knowledge-to-carousel")!;
  const jobId = "merge-ui-controlled";
  const makeJob = (status: "ready" | "saved", digest: string, skillMd: string, savedSkills: Array<Record<string, unknown>> = []) => ({
    id: jobId, request_id: "merge-ui-controlled-request", task_kind: "merge", mode: "single",
    instruction: "", source_text: "", suggested_topic: null, revision: 1, operation: null, status,
    created_at: "2026-10-05T00:00:00Z", updated_at: "2026-10-05T00:00:01Z", uploads: [],
    source_skills: [mind, visual].map(item => ({ id: item.id, name: item.name, role: item.role, digest: "source-digest" })),
    files: [
      { role: "legacy_end_to_end", path: "SKILL.md", kind: "text", url: `/api/skill-extractions/${jobId}/files?role=legacy_end_to_end&path=SKILL.md` },
      { role: "legacy_end_to_end", path: "assets/reference-01.png", kind: "image", url: `/api/skill-extractions/${jobId}/files?role=legacy_end_to_end&path=assets%2Freference-01.png` },
    ],
    thread_id: "controlled-native-merge", error: null, note: "受控 E2E 融合响应；不是 SDK/模型验收。",
    skills: [{ name: "knowledge-to-carousel", role: "legacy_end_to_end", output_kind: "image-carousel", skill_md: skillMd }],
    digest, saved_skills: savedSkills, progress: null, trials: [],
  });
  const originalSkill = "---\nname: knowledge-to-carousel\ndescription: 受控融合草稿\n---\n\n# 融合规则\n\n受控版本一。";
  let job = makeJob("ready", "merge-digest-v1", originalSkill);

  await page.route("**/api/skill-extractions/**", async route => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    if (path === "/api/skill-extractions/merge" && req.method() === "POST") {
      mergeCount += 1;
      const body = req.postDataJSON();
      expect(body.skill_ids).toEqual([mind.id, visual.id]);
      await route.fulfill({ status: 202, json: { ...job, request_id: body.request_id } });
    } else if (path === `/api/skill-extractions/${jobId}/events` && req.method() === "GET") {
      await route.fulfill({ status: 404, json: { error: { message: "受控任务无公开事件。" } } });
    } else if (path === `/api/skill-extractions/${jobId}/files` && req.method() === "GET") {
      if (url.searchParams.get("path")?.endsWith(".png")) {
        await route.fulfill({ contentType: "image/png", body: tinyPng });
      } else {
        await route.fulfill({ contentType: "text/plain; charset=utf-8", body: originalSkill });
      }
    } else if (path === `/api/skill-extractions/${jobId}/draft` && req.method() === "POST") {
      draftSaveCount += 1;
      const body = req.postDataJSON();
      job = makeJob("ready", "merge-digest-v2", body.skills[0].skill_md);
      await route.fulfill({ json: job });
    } else if (path === `/api/skill-extractions/${jobId}/save` && req.method() === "POST") {
      saveCount += 1;
      const body = req.postDataJSON();
      expect(body.expected_digest).toBe(job.digest);
      job = makeJob("saved", job.digest, job.skills[0].skill_md, [savedSkill]);
      await route.fulfill({ json: job });
    } else if (path === `/api/skill-extractions/${jobId}` && req.method() === "GET") {
      await route.fulfill({ json: job });
    } else if (path === "/api/skill-extractions" && req.method() === "GET") {
      await route.fulfill({ json: { items: [job] } });
    } else {
      await route.continue();
    }
  });

  page.on("request", event => {
    if (event.method() === "POST" && new URL(event.url()).pathname === "/api/series") seriesWriteCount += 1;
  });
  await page.goto("/skills");
  const basket = page.getByLabel("Skill 组合框", { exact: true });
  await page.getByRole("button", { name: "inspector-mind", exact: true }).click();
  await page.getByRole("dialog", { name: "inspector-mind" }).getByRole("button", { name: "加入组合" }).click();
  await page.getByRole("button", { name: "inspector-visual", exact: true }).click();
  await page.getByRole("dialog", { name: "inspector-visual" }).getByRole("button", { name: "加入组合" }).click();
  await page.getByRole("button", { name: "生成融合草稿", exact: true }).click();
  await expect(page.getByText("任务状态：待确认")).toBeVisible();
  await expect(page.getByText(/受控 E2E 融合响应/)).toBeVisible();
  expect(mergeCount).toBe(1);
  expect(seriesWriteCount).toBe(0);
  await expect(page.getByRole("button", { name: "创建栏目", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "确认加入 Skill 库" })).toBeVisible();
  expect(saveCount).toBe(0);
  await page.getByRole("button", { name: /assets\/reference-01\.png/ }).click();
  await expect.poll(() => page.locator(".extraction-asset-preview").evaluate((image: HTMLImageElement) => image.naturalWidth > 0)).toBe(true);
  await page.getByRole("button", { name: /SKILL\.md/ }).click();

  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await page.getByLabel("编辑 SKILL.md").fill(originalSkill.replace("受控版本一。", "未保存的受控版本二。"));
  await expect(page.getByRole("button", { name: "生成融合草稿", exact: true })).toBeDisabled();
  await expect(page.getByRole("alert")).toContainText("先保存上方工作台的草稿修改");
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.getByText("有未保存的草稿修改")).toHaveCount(0);
  expect(draftSaveCount).toBe(1);
  expect(mergeCount).toBe(1);

  await page.getByRole("button", { name: "确认加入 Skill 库", exact: true }).click();
  await expect(page.getByText("已加入 CreatorOS Skill 库")).toBeVisible();
  expect(saveCount).toBe(1);
  await page.getByRole("button", { name: "用「knowledge-to-carousel」创建栏目", exact: true }).click();
  await expect(basket.getByRole("button", { name: "移除 knowledge-to-carousel", exact: true })).toHaveCount(1);
  await expect(basket.getByRole("button", { name: "移除 inspector-mind", exact: true })).toHaveCount(0);
  expect(seriesWriteCount).toBe(0);
  await page.getByLabel("栏目名称", { exact: true }).fill("融合后显式创建");

  const composeRequest = page.waitForRequest(event => event.method() === "POST" && new URL(event.url()).pathname === "/api/series");
  const composeResponse = page.waitForResponse(response => response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/series");
  await page.getByRole("button", { name: "创建栏目", exact: true }).click();
  const sent = await composeRequest;
  expect(sent.postDataJSON()).toMatchObject({ skill_name: "knowledge-to-carousel" });
  const response = await composeResponse;
  expect(response.status(), await response.text()).toBe(201);
  expect(seriesWriteCount).toBe(1);
  const result = await response.json();
  expect(result.series.skill_name).toBe("knowledge-to-carousel");
});

test("a single Mind Skill is rejected by the real API and never shown as a created series", async ({ page, request }) => {
  await page.goto("/skills");
  await page.getByRole("button", { name: "inspector-mind", exact: true }).click();
  await page.getByRole("dialog", { name: "inspector-mind" }).getByRole("button", { name: "加入组合" }).click();
  const name = `单 Mind 不可生产-${crypto.randomUUID().slice(0, 8)}`;
  await page.getByLabel("栏目名称", { exact: true }).fill(name);

  const composeResponse = page.waitForResponse(response => response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/series");
  await page.getByRole("button", { name: "创建栏目", exact: true }).click();
  const response = await composeResponse;
  expect(response.status()).toBe(422);
  await expect(page.getByRole("alert")).toContainText("不能单独承担图片轮播生产");
  await expect(page.getByText(`已创建「${name}」。`)).toHaveCount(0);
  const series = await request.get("/api/series").then(reply => reply.json());
  expect(series).not.toEqual(expect.arrayContaining([expect.objectContaining({ name })]));
});
