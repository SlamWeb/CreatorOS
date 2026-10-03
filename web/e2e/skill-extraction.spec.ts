import { expect, test, type Page } from "@playwright/test";

const tinyPng = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/pC8AAAAASUVORK5CYII=", "base64");

function fakeJob(status: string, overrides: Record<string, unknown> = {}) {
  return {
    id: "extract-e2e", request_id: "request-e2e", mode: "single", instruction: "保持克制",
    status, created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    uploads: [{ id: "upload-e2e", name: "参考.png", url: "/api/skill-extractions/uploads/upload-e2e" }],
    thread_id: "thread-e2e", error: null, note: null,
    skills: status === "ready" || status === "saved" ? [
      { name: "作品完整 Skill", role: "legacy_end_to_end", output_kind: "image-carousel", skill_md: "---\nname: work-skill\ndescription: 可迁移的完整制作方法\n---\n\n# 提炼规则\n\n保留清晰层级。" },
    ] : [],
    digest: status === "ready" || status === "saved" ? "digest-e2e-v1" : null,
    files: status === "ready" || status === "saved" ? [
      { role: "legacy_end_to_end", path: "SKILL.md", kind: "text", url: "/api/skill-extractions/extract-e2e/files?role=legacy_end_to_end&path=SKILL.md" },
      { role: "legacy_end_to_end", path: "assets/reference-01.png", kind: "image", url: "/api/skill-extractions/extract-e2e/files?role=legacy_end_to_end&path=assets%2Freference-01.png" },
    ] : [],
    revision: 1, suggested_topic: "沿用参考作品的内容试做，保留内容与呈现特点。", source_text: "",
    operation: null, trials: [],
    saved_skills: status === "saved" ? [{ id: "saved-e2e", name: "作品完整 Skill", description: "提炼自作品", carousel_compatible: true, compatibility_note: "", commit: null, github_url: null, role: "legacy_end_to_end", producible: true, local_path: "D:/isolated/producer-skills/work-skill/SKILL.md" }] : [],
    progress: null,
    ...overrides,
  };
}

async function installControlledApi(page: Page, initialStatus = "ready", dropFirstCreateResponse = false) {
  let job = fakeJob("interrupted");
  const posts: Array<{ path: string; body: unknown }> = [];
  let skillsReadCount = 0;
  let draftVersion = 1;
  let catalogSaved = false;
  await page.route("**/api/producer-skills", async route => {
    skillsReadCount += 1;
    const items = catalogSaved ? [{ id: "saved-e2e", name: "作品完整 Skill", description: "提炼自作品", carousel_compatible: true, compatibility_note: "", commit: null, github_url: null, role: "legacy_end_to_end", producible: true, local_path: "D:/isolated/producer-skills/work-skill/SKILL.md" }] : [];
    await route.fulfill({ json: { items } });
  });
  await page.route("**/api/skill-extractions**", async route => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;
    if (pathname.endsWith("/uploads") && request.method() === "POST") {
      const body = request.postDataJSON();
      posts.push({ path: pathname, body });
      await route.fulfill({ json: { id: "upload-e2e", name: body.name, url: "/api/skill-extractions/uploads/upload-e2e" } });
    } else if (pathname.endsWith("/files") && request.method() === "GET") {
      const filePath = url.searchParams.get("path") ?? "";
      if (filePath.endsWith(".png")) await route.fulfill({ contentType: "image/png", body: tinyPng });
      else await route.fulfill({ contentType: "text/plain; charset=utf-8", body: "controlled reference text" });
    } else if (pathname.includes("/trials/") && pathname.includes("/cards/") && request.method() === "GET") {
      await route.fulfill({ contentType: "image/svg+xml", body: "<svg xmlns='http://www.w3.org/2000/svg' width='320' height='480'><rect width='320' height='480' fill='#d9e5ee'/><text x='32' y='240' font-size='24' fill='#24384a'>Controlled trial</text></svg>" });
    } else if (pathname === "/api/skill-extractions" && request.method() === "POST") {
      const body = request.postDataJSON();
      posts.push({ path: pathname, body });
      if (job.status !== "interrupted" && body.request_id === job.request_id) {
        await route.fulfill({ json: job });
        return;
      }
      const nextStatus = body.instruction === "模拟失败" ? "failed" : initialStatus;
      job = fakeJob(nextStatus, { request_id: body.request_id, mode: body.mode, instruction: body.instruction,
        source_text: body.source_text ?? "", uploads: (body.upload_ids ?? []).map((id: string) => ({ id, name: "参考.png", url: `/api/skill-extractions/uploads/${id}` })),
        error: nextStatus === "failed" ? "受控测试：Codex 暂不可用" : null });
      if (dropFirstCreateResponse) {
        dropFirstCreateResponse = false;
        await route.abort();
        return;
      }
      await route.fulfill({ json: job });
    } else if (pathname === "/api/skill-extractions" && request.method() === "GET") {
      await route.fulfill({ json: { items: job.status === "interrupted" ? [] : [job] } });
    } else if (pathname === `/api/skill-extractions/${job.id}/save` && request.method() === "POST") {
      const body = request.postDataJSON();
      posts.push({ path: pathname, body });
      if (body.expected_digest !== job.digest) {
        await route.fulfill({ status: 409, json: { error: { message: "草稿摘要已变化，请重新检查。" } } });
        return;
      }
      catalogSaved = true;
      job = fakeJob("saved", { request_id: job.request_id, mode: job.mode, instruction: job.instruction });
      await route.fulfill({ json: job });
    } else if (pathname === `/api/skill-extractions/${job.id}/draft` && request.method() === "POST") {
      const body = request.postDataJSON();
      posts.push({ path: pathname, body });
      if (body.expected_digest !== job.digest) {
        await route.fulfill({ status: 409, json: { error: { message: "草稿摘要已变化，请重新检查。" } } });
        return;
      }
      draftVersion += 1;
      job = fakeJob("ready", { ...job, skills: body.skills, digest: `digest-e2e-v${draftVersion}`, revision: draftVersion });
      await route.fulfill({ json: job });
    } else if (pathname === `/api/skill-extractions/${job.id}/revise` && request.method() === "POST") {
      const body = request.postDataJSON();
      posts.push({ path: pathname, body });
      draftVersion += 1;
      job = fakeJob("ready", { ...job, digest: `digest-revised-${draftVersion}`, operation: null,
        revision: draftVersion, skills: job.skills.map((skill: { skill_md: string }) => ({
          ...skill, skill_md: skill.skill_md.replace("保留清晰层级。", "改稿完成：保留清晰层级。"),
        })) });
      await route.fulfill({ status: 202, json: job });
    } else if (pathname === `/api/skill-extractions/${job.id}/trials` && request.method() === "POST") {
      const body = request.postDataJSON();
      posts.push({ path: pathname, body });
      const trial = { id: "trial-e2e", status: "completed", digest: body.expected_digest, topic: body.topic,
        error: null, progress: null, thread_id: "trial-thread", cards: [
          { order: 1, url: "/api/skill-extractions/extract-e2e/trials/trial-e2e/cards/1", image_prompt: "controlled" },
        ] };
      job = fakeJob("ready", { ...job, trials: [...(job.trials ?? []), trial], operation: null });
      await route.fulfill({ status: 202, json: job });
    } else if (pathname === `/api/skill-extractions/${job.id}/cancel` && request.method() === "POST") {
      posts.push({ path: pathname, body: request.postDataJSON() });
      job = fakeJob("running", { cancel_requested: true });
      await route.fulfill({ json: job });
    } else if (pathname === `/api/skill-extractions/${job.id}` && request.method() === "GET") {
      await route.fulfill({ json: job });
    } else {
      await route.fulfill({ status: 404, json: { error: { message: "受控测试中不存在此提炼资源。" } } });
    }
  });
  return { posts, skillReads: () => skillsReadCount };
}

async function submitOneImage(page: Page, instruction = "保持克制") {
  await page.goto("/skills");
  await page.getByRole("button", { name: "从作品提炼" }).click();
  await page.getByLabel("参考图片").setInputFiles({ name: "参考.png", mimeType: "image/png", buffer: tinyPng });
  await page.getByLabel("提炼要求").fill(instruction);
  await page.getByRole("button", { name: "开始提炼" }).click();
}

test("uploads, creates one persisted job, resumes after refresh, previews and confirms save", async ({ page }, testInfo) => {
  const api = await installControlledApi(page);
  await submitOneImage(page);
  await expect(page.getByText("任务状态：待确认")).toBeVisible();
  await expect(page.getByText("保留清晰层级。")).toBeVisible();
  await page.getByRole("button", { name: /assets\/reference-01.png/ }).click();
  await expect(page.locator(".extraction-asset-preview")).toBeVisible();
  await page.getByRole("button", { name: "SKILL.md" }).click();
  expect(api.posts.filter(item => item.path.endsWith("/uploads"))).toHaveLength(1);
  const createRequest = api.posts.find(item => item.path === "/api/skill-extractions");
  expect(createRequest).toBeTruthy();
  expect(createRequest!.body).toMatchObject({ upload_ids: ["upload-e2e"], mode: "single", instruction: "保持克制", source_text: "" });
  expect((createRequest!.body as { request_id: string }).request_id).toBeTruthy();
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`skill-extraction-ready-${viewport.width}.png`), fullPage: true });
  }
  await page.reload();
  await expect(page.getByText("任务状态：待确认")).toBeVisible();
  expect(api.posts.filter(item => item.path === "/api/skill-extractions")).toHaveLength(1);
  await page.getByRole("button", { name: "确认加入 Skill 库" }).click();
  await expect(page.getByText("已加入 CreatorOS Skill 库")).toBeVisible();
  await expect(page.getByText(/本地路径：D:\/isolated\/producer-skills\/work-skill\/SKILL.md/)).toBeVisible();
  await expect.poll(api.skillReads).toBeGreaterThan(1);
  await expect(page.getByRole("button", { name: /作品完整 Skill/ })).toBeVisible();
  expect(api.posts.filter(item => item.path.endsWith("/save"))).toHaveLength(1);
});

test("edits the visible Skill, saves a new digest and labels trials from older drafts", async ({ page }, testInfo) => {
  const api = await installControlledApi(page);
  await submitOneImage(page);
  await expect(page.getByText("任务状态：待确认")).toBeVisible();
  await page.getByLabel("完整 Skill Frontmatter 名称").fill("edited-skill");
  await page.reload();
  await expect(page.getByLabel("完整 Skill Frontmatter 名称")).toHaveValue("edited-skill");
  await expect(page.getByText("有未保存的草稿修改")).toBeVisible();
  await page.getByRole("button", { name: "保存草稿" }).click();
  await expect(page.getByText("有未保存的草稿修改")).toHaveCount(0);
  const draftSave = api.posts.find(item => item.path.endsWith("/draft"));
  expect(draftSave?.body).toMatchObject({ expected_digest: "digest-e2e-v1" });
  expect((draftSave?.body as { skills: Array<{ name: string }> }).skills[0].name).toBe("edited-skill");
  await expect(page.getByText("旧草稿结果")).toHaveCount(0);

  await page.getByLabel("试产选题").fill("沿用样例主题试做");
  await page.getByRole("button", { name: "主动开始试产" }).click();
  await expect(page.getByText("试产完成")).toBeVisible();
  await expect.poll(() => page.locator(".extraction-trial-cards img").evaluate((image: HTMLImageElement) => image.naturalWidth > 0)).toBe(true);
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`skill-workbench-trial-${viewport.width}.png`), fullPage: true });
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  const trialRequest = api.posts.find(item => item.path.endsWith("/trials"));
  expect(trialRequest?.body).toMatchObject({ expected_digest: "digest-e2e-v2", topic: "沿用样例主题试做" });

  await page.getByRole("button", { name: "编辑" }).click();
  await page.getByLabel("编辑 SKILL.md").fill("---\nname: third-version\ndescription: updated\n---\n\nKeep the transferable rules.");
  await page.getByRole("button", { name: "保存草稿" }).click();
  await expect(page.getByText("旧草稿结果")).toBeVisible();
  expect(api.posts.filter(item => item.path.endsWith("/draft"))).toHaveLength(2);
});

test("submits source text and user instructions as separate single-mode fields", async ({ page }) => {
  const api = await installControlledApi(page);
  await page.goto("/skills");
  await page.getByRole("button", { name: "从作品提炼" }).click();
  await page.getByLabel("参考文案").fill("这是作为样例分析的原始作品文案。");
  await page.getByLabel("提炼要求").fill("保留双语节奏，但不要固定主题。");
  await page.getByRole("button", { name: "开始提炼" }).click();
  await expect(page.getByText("任务状态：待确认")).toBeVisible();
  const create = api.posts.find(item => item.path === "/api/skill-extractions");
  expect(create?.body).toMatchObject({ upload_ids: [], mode: "single", source_text: "这是作为样例分析的原始作品文案。",
    instruction: "保留双语节奏，但不要固定主题。" });
  expect(api.posts.filter(item => item.path.endsWith("/uploads"))).toHaveLength(0);
});

test("asks Codex to revise the current digest without saving or starting a trial", async ({ page }) => {
  const api = await installControlledApi(page);
  await submitOneImage(page);
  await expect(page.getByText("任务状态：待确认")).toBeVisible();
  await page.getByLabel("要求 Codex 修改草稿").fill("保留内容，但把结尾写得更明确。");
  await page.getByRole("button", { name: "让 Codex 改稿" }).click();
  await expect(page.getByText("改稿完成：保留清晰层级。")).toBeVisible();
  const revision = api.posts.find(item => item.path.endsWith("/revise"));
  expect(revision?.body).toMatchObject({ expected_digest: "digest-e2e-v1", instruction: "保留内容，但把结尾写得更明确。" });
  expect((revision?.body as { request_id: string }).request_id).toBeTruthy();
  expect(api.posts.filter(item => item.path.endsWith("/save"))).toHaveLength(0);
  expect(api.posts.filter(item => item.path.endsWith("/trials"))).toHaveLength(0);
});

test("reuses the same request and uploads when the server accepted but the response was lost", async ({ page }) => {
  const api = await installControlledApi(page, "ready", true);
  await submitOneImage(page);
  await expect(page.getByRole("alert")).toContainText("无法连接");
  await page.getByRole("button", { name: "开始提炼" }).click();
  await expect(page.getByText("任务状态：待确认")).toBeVisible();
  const creates = api.posts.filter(item => item.path === "/api/skill-extractions");
  const uploads = api.posts.filter(item => item.path.endsWith("/uploads"));
  expect(creates).toHaveLength(2);
  expect(uploads).toHaveLength(1);
  expect(creates[0].body).toEqual(creates[1].body);
});

test("shows a server extraction failure without offering save", async ({ page }) => {
  const api = await installControlledApi(page);
  await submitOneImage(page, "模拟失败");
  await expect(page.getByText("受控测试：Codex 暂不可用")).toBeVisible();
  await expect(page.getByRole("button", { name: "确认加入 Skill 库" })).toHaveCount(0);
  expect(api.posts.filter(item => item.path === "/api/skill-extractions")).toHaveLength(1);
});

test("cancels a running extraction explicitly", async ({ page }) => {
  const api = await installControlledApi(page, "running");
  await submitOneImage(page);
  await expect(page.getByText("任务状态：进行中")).toBeVisible();
  await page.getByRole("button", { name: "取消当前操作" }).click();
  await expect(page.getByText("正在取消当前操作…")).toBeVisible();
  await expect(page.getByRole("button", { name: "取消当前操作" })).toHaveCount(0);
  expect(api.posts.some(item => item.path.endsWith("/cancel"))).toBe(true);
});
