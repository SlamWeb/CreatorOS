import { expect, test, type Page } from "@playwright/test";

const tinyPng = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/pC8AAAAASUVORK5CYII=", "base64");

function fakeJob(status: string, overrides: Record<string, unknown> = {}) {
  return {
    id: "extract-e2e", request_id: "request-e2e", mode: "pair", instruction: "保持克制",
    status, created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    uploads: [{ id: "upload-e2e", name: "参考.png", url: "/api/skill-extractions/uploads/upload-e2e" }],
    thread_id: "thread-e2e", error: null, note: null,
    skills: status === "ready" || status === "saved" ? [
      { name: "作品 Mind", role: "mind", skill_md: "---\nname: work-mind\n---\n\n# 提炼规则\n\n保留清晰层级。" },
      { name: "作品 Visualize", role: "production", skill_md: "---\nname: work-visual\n---\n\n# 视觉规则\n\n留白优先。" },
    ] : [],
    digest: status === "ready" || status === "saved" ? "digest-e2e" : null,
    saved_skills: status === "saved" ? [{ id: "saved-e2e", name: "作品 Mind", description: "提炼自作品", carousel_compatible: true, compatibility_note: "", commit: null, github_url: null, role: "mind", producible: true, local_path: "D:/isolated/producer-skills/work-mind/SKILL.md" }] : [],
    progress: null,
    ...overrides,
  };
}

async function installControlledApi(page: Page, initialStatus = "ready", dropFirstCreateResponse = false) {
  let job = fakeJob("interrupted");
  const posts: Array<{ path: string; body: unknown }> = [];
  let skillsReadCount = 0;
  let catalogSaved = false;
  await page.route("**/api/producer-skills", async route => {
    skillsReadCount += 1;
    const items = catalogSaved ? [{ id: "saved-e2e", name: "作品 Mind", description: "提炼自作品", carousel_compatible: true, compatibility_note: "", commit: null, github_url: null, role: "mind", producible: true, local_path: "D:/isolated/producer-skills/work-mind/SKILL.md" }] : [];
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
    } else if (pathname === "/api/skill-extractions" && request.method() === "POST") {
      const body = request.postDataJSON();
      posts.push({ path: pathname, body });
      if (job.status !== "interrupted" && body.request_id === job.request_id) {
        await route.fulfill({ json: job });
        return;
      }
      const nextStatus = body.instruction === "模拟失败" ? "failed" : initialStatus;
      job = fakeJob(nextStatus, { request_id: body.request_id, mode: body.mode, instruction: body.instruction,
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
  await page.getByLabel("补充要求").fill(instruction);
  await page.getByRole("button", { name: "开始提炼" }).click();
}

test("uploads, creates one persisted job, resumes after refresh, previews and confirms save", async ({ page }, testInfo) => {
  const api = await installControlledApi(page);
  await submitOneImage(page);
  await expect(page.getByText("任务状态：待确认")).toBeVisible();
  await expect(page.getByText("保留清晰层级。")).toBeVisible();
  expect(api.posts.filter(item => item.path.endsWith("/uploads"))).toHaveLength(1);
  const createRequest = api.posts.find(item => item.path === "/api/skill-extractions");
  expect(createRequest).toBeTruthy();
  expect(createRequest!.body).toMatchObject({ upload_ids: ["upload-e2e"], mode: "pair", instruction: "保持克制" });
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
  await expect(page.getByText("已加入 Skill 库")).toBeVisible();
  await expect(page.getByText(/可在本地编辑：D:\/isolated\/producer-skills\/work-mind\/SKILL.md/)).toBeVisible();
  await expect.poll(api.skillReads).toBeGreaterThan(1);
  await expect(page.getByRole("button", { name: "作品 Mind" })).toBeVisible();
  expect(api.posts.filter(item => item.path.endsWith("/save"))).toHaveLength(1);
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
  await page.getByRole("button", { name: "取消提炼" }).click();
  await expect(page.getByText("正在取消任务…")).toBeVisible();
  await expect(page.getByRole("button", { name: "取消提炼" })).toHaveCount(0);
  expect(api.posts.some(item => item.path.endsWith("/cancel"))).toBe(true);
});
