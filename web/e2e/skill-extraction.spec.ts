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

async function installControlledApi(page: Page, initialStatus = "ready", dropFirstCreateResponse = false,
  switchEventStream = false, noEventHistory = false, updateActivity = false, paginateEvents = false) {
  let job = fakeJob("interrupted");
  const posts: Array<{ path: string; body: unknown }> = [];
  let eventReads = 0;
  let latestEventReads = 0;
  let skillsReadCount = 0;
  let draftVersion = 1;
  let catalogSaved = false;
  const makeEvents = (streamId: string) => {
    if (paginateEvents) return Array.from({ length: 101 }, (_, index) => ({
      id: index + 1, kind: "status", title: `活动 ${index + 1}`, status: "completed",
      at: "2026-10-05T01:00:00Z", updated_at: "2026-10-05T01:00:01Z", text: `活动正文 ${index + 1}`,
      truncated: false, redacted: false,
    }));
    const toolRunning = updateActivity && eventReads === 1;
    const message = streamId === "stream-e2e-1"
      ? `## Codex 公开进度\n${"正文片段。".repeat(120)}\n完整消息结尾标记`
      : "新 stream 的真实公开消息";
    const toolText = toolRunning ? `工具仍在运行\n${"临时输出片段。".repeat(80)}`
      : updateActivity ? "工具完成结果：草稿文件已读取完毕。" : "工具读取失败：权限不足";
    const all = [
      { id: 1, kind: "message", title: streamId === "stream-e2e-1" ? "Codex 进度消息" : "新 stream 消息", status: "completed",
        at: "2026-10-05T01:00:00Z", updated_at: "2026-10-05T01:00:01Z", text: message,
        truncated: message.length > 500, redacted: false, item_type: "agentMessage", item_id: "message-e2e" },
      { id: 2, kind: "tool", title: "读取草稿文件", status: toolRunning ? "running" : updateActivity ? "completed" : "failed", at: "2026-10-05T01:00:02Z",
        updated_at: toolRunning ? "2026-10-05T01:00:03Z" : "2026-10-05T01:00:04Z", text: toolText, truncated: toolText.length > 500, redacted: false,
        item_type: "commandExecution", item_id: "tool-e2e" },
    ];
    return all.map(item => ({ ...item, text: item.text.slice(0, 500) }));
  };
  const fullEvent = (eventId: number, streamId: string) => {
    if (eventId === 1) return {
      id: 1, kind: "message", title: streamId === "stream-e2e-1" ? "Codex 进度消息" : "新 stream 消息", status: "completed",
      at: "2026-10-05T01:00:00Z", updated_at: "2026-10-05T01:00:01Z",
      text: streamId === "stream-e2e-1" ? `## Codex 公开进度\n${"正文片段。".repeat(120)}\n完整消息结尾标记` : "新 stream 的真实公开消息",
      truncated: false, redacted: false, item_type: "agentMessage", item_id: "message-e2e",
    };
    const completed = updateActivity && eventReads > 1;
    const toolText = completed ? "工具完成结果：草稿文件已读取完毕。"
      : updateActivity ? `工具仍在运行\n${"临时输出片段。".repeat(80)}` : "工具读取失败：权限不足";
    return { id: 2, kind: "tool", title: "读取草稿文件", status: completed ? "completed" : updateActivity ? "running" : "failed", at: "2026-10-05T01:00:02Z",
      updated_at: completed ? "2026-10-05T01:00:04Z" : "2026-10-05T01:00:03Z", text: toolText, truncated: toolText.length > 500, redacted: false,
      item_type: "commandExecution", item_id: "tool-e2e" };
  };
  await page.route("**/api/producer-skills", async route => {
    skillsReadCount += 1;
    const items = catalogSaved ? [{ id: "saved-e2e", name: "作品完整 Skill", description: "提炼自作品", carousel_compatible: true, compatibility_note: "", commit: null, github_url: null, role: "legacy_end_to_end", producible: true, local_path: "D:/isolated/producer-skills/work-skill/SKILL.md" }] : [];
    await route.fulfill({ json: { items } });
  });
  await page.route("**/api/skill-extractions**", async route => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;
    const eventsPath = pathname.match(/^\/api\/skill-extractions\/[^/]+\/events$/);
    const eventDetailPath = pathname.match(/^\/api\/skill-extractions\/[^/]+\/events\/(\d+)$/);
    if (eventsPath && request.method() === "GET") {
      eventReads += 1;
      if (noEventHistory) {
        await route.fulfill({ status: 404, json: { error: { message: "此任务没有公开活动记录。" } } });
        return;
      }
      const streamId = switchEventStream && eventReads > 1 ? "stream-e2e-2" : "stream-e2e-1";
      const beforeId = Number(url.searchParams.get("before_id") ?? 0);
      if (beforeId === 0) latestEventReads += 1;
      const limit = Number(url.searchParams.get("limit") ?? 50);
      const events = makeEvents(streamId).filter(item => beforeId === 0 || item.id < beforeId);
      await route.fulfill({ json: { stream_id: streamId, items: events.slice(-limit), has_more: events.length > limit } });
    } else if (eventDetailPath && request.method() === "GET") {
      const streamId = url.searchParams.get("stream_id") ?? "stream-e2e-1";
      await route.fulfill({ json: fullEvent(Number(eventDetailPath[1]), streamId) });
    } else if (pathname.endsWith("/uploads") && request.method() === "POST") {
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
  return { posts, skillReads: () => skillsReadCount, eventReads: () => eventReads, latestEventReads: () => latestEventReads };
}

async function submitOneImage(page: Page, instruction = "保持克制") {
  await page.goto("/skills");
  await page.getByRole("button", { name: "从作品提炼" }).click();
  await page.getByLabel("参考图片", { exact: true }).setInputFiles({ name: "参考.png", mimeType: "image/png", buffer: tinyPng });
  await page.getByLabel("提炼要求").fill(instruction);
  await page.getByRole("button", { name: "开始提炼" }).click();
}

test("nine renamed references deduplicate through real upload and Mind submission", async ({ page, request }, info) => {
  // No browser API stubs: isolated HTTP/files are real; the server's extractor is controlled.
  await page.goto("/skills");
  await page.getByRole("button", { name: "从作品提炼", exact: true }).click();
  const samples = await page.evaluate(() => Array.from({ length: 8 }, (_, i) => {
    const canvas = document.createElement("canvas"); canvas.width = 90; canvas.height = 120;
    const context = canvas.getContext("2d")!;
    context.fillStyle = `hsl(${i * 40},60%,80%)`; context.fillRect(0, 0, 90, 120);
    return canvas.toDataURL("image/png").split(",")[1];
  }));
  const files = [...samples, samples[0]].map((data, i) => ({ name: `reference-${i + 1}.png`, mimeType: "image/png", buffer: Buffer.from(data, "base64") }));
  await page.getByLabel("参考图片", { exact: true }).setInputFiles(files);
  await expect(page.getByLabel("已选图片").getByRole("img")).toHaveCount(9);
  await page.getByLabel("提炼方式", { exact: true }).selectOption("mind");
  await page.getByLabel("提炼要求", { exact: true }).fill("只提炼如何讲故事和内容，不用管IP形象");
  const submitted = page.waitForResponse(r => r.url().endsWith("/api/skill-extractions") && r.request().method() === "POST");
  await page.getByRole("button", { name: "开始提炼", exact: true }).click();
  const response = await submitted;
  expect(response.status()).toBe(202);
  const sent = response.request().postDataJSON();
  expect(sent.mode).toBe("mind"); expect(sent.source_text).toBe("");
  expect(sent.instruction).toBe("只提炼如何讲故事和内容，不用管IP形象");
  expect(sent.upload_ids).toHaveLength(8); expect(new Set(sent.upload_ids).size).toBe(8);
  const jobId = (await response.json()).id;
  await expect(page.getByText("任务状态：待确认")).toBeVisible();
  await expect(page.getByText("已合并 1 张重复图片，使用 8 张参考图。", { exact: true })).toBeVisible();
  const job = await (await request.get(`/api/skill-extractions/${jobId}`)).json();
  expect(job.upload_ids).toEqual(sent.upload_ids); expect(job.skills.map((s: { role: string }) => s.role)).toEqual(["mind"]);
  for (let i = 0; i < 8; i++) {
    expect(await (await request.get(job.uploads[i].url)).body()).toEqual(files[i].buffer);
  }
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: info.outputPath(`duplicate-mind-${width}.png`), fullPage: true });
  }
  await page.reload(); await expect(page.getByText("任务状态：待确认")).toBeVisible();
  expect(new URL(page.url()).searchParams.get("extraction")).toBe(jobId);
});

test("duplicate references retry an unknown submission without reupload or a new request", async ({ page }) => {
  const api = await installControlledApi(page, "ready", true);
  await page.goto("/skills"); await page.getByRole("button", { name: "从作品提炼", exact: true }).click();
  await page.getByLabel("参考图片", { exact: true }).setInputFiles(["first.png", "renamed.png"].map(name => ({ name, mimeType: "image/png", buffer: tinyPng })));
  await page.getByLabel("提炼方式", { exact: true }).selectOption("mind");
  await page.getByRole("button", { name: "开始提炼", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByLabel("已选图片").getByRole("img")).toHaveCount(2);
  await page.getByRole("button", { name: "开始提炼", exact: true }).click();
  await expect(page.getByText("任务状态：待确认")).toBeVisible();
  const creates = api.posts.filter(p => p.path === "/api/skill-extractions");
  expect(creates).toHaveLength(2); expect(creates[0].body).toEqual(creates[1].body);
  expect((creates[0].body as { upload_ids: string[] }).upload_ids).toEqual(["upload-e2e"]);
  expect(api.posts.filter(p => p.path.endsWith("/uploads"))).toHaveLength(2);
});

test("long real-world draft paths wrap inside the mobile extraction detail", async ({ page }) => {
  await installControlledApi(page);
  const note = `已完成草稿：D:/CreatorOS/tmp/isolated/studio-producer-skills/extractions/jobs/${"a".repeat(64)}/draft/skill/SKILL.md；未入库。`;
  await page.route("**/api/skill-extractions/extract-e2e", route => route.fulfill({ json: fakeJob("ready", { note }) }));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/skills?extraction=extract-e2e");
  await expect(page.getByText(note, { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "SKILL.md", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("batch image selection appends drops, previews, removes and submits one ordered task", async ({ page }, info) => {
  const api = await installControlledApi(page);
  const uploads: string[] = [];
  await page.route("**/api/skill-extractions/uploads", async route => {
    const name = route.request().postDataJSON().name;
    uploads.push(name);
    await route.fulfill({ json: { id: name, name, url: `/api/skill-extractions/uploads/${name}` } });
  });
  await page.goto("/skills"); await page.getByRole("button", { name: "从作品提炼", exact: true }).click();
  await page.screenshot({ path: info.outputPath("batch-empty-desktop.png"), fullPage: true });
  const samples = await page.evaluate(() => Array.from({ length: 15 }, (_, index) => {
    const canvas = document.createElement("canvas"); canvas.width = 180; canvas.height = 240;
    const ctx = canvas.getContext("2d")!;
    ctx.fillStyle = `hsl(${index * 24},60%,85%)`; ctx.fillRect(0, 0, 180, 240);
    ctx.fillStyle = "#163655"; ctx.font = "24px sans-serif"; ctx.fillText(`SAMPLE ${index + 1}`, 16, 130);
    return canvas.toDataURL("image/png").split(",")[1];
  }));
  const images = samples.map((data, index) => ({ name: `work-${index + 1}.png`, mimeType: "image/png", buffer: Buffer.from(data, "base64") }));
  await page.getByLabel("参考图片", { exact: true }).setInputFiles(images);
  await expect(page.getByLabel("已选图片").getByRole("img")).toHaveCount(15);
  const drop = await page.evaluateHandle(data => {
    const transfer = new DataTransfer();
    transfer.items.add(new File([Uint8Array.from(atob(data), c => c.charCodeAt(0))], "dropped.png", { type: "image/png" }));
    return transfer;
  }, tinyPng.toString("base64"));
  await page.getByRole("group", { name: "参考图片上传区" }).dispatchEvent("drop", { dataTransfer: drop }); await drop.dispose();
  await expect(page.getByLabel("已选图片").getByRole("img")).toHaveCount(16);
  await page.getByRole("button", { name: "移除 work-3.png", exact: true }).click();
  await expect(page.getByLabel("已选图片").getByRole("img")).toHaveCount(15);
  const popupPromise = page.waitForEvent("popup");
  await page.getByRole("link", { name: "预览 work-1.png", exact: true }).click();
  const popup = await popupPromise; await popup.waitForLoadState(); expect(popup.url()).toMatch(/^blob:/); await popup.close();
  await page.screenshot({ path: info.outputPath("batch-selected-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: info.outputPath("batch-selected-mobile.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("button", { name: "开始提炼", exact: true }).click();
  await expect(page.getByText("任务状态：待确认")).toBeVisible();
  const expected = [...images.filter(item => item.name !== "work-3.png").map(item => item.name), "dropped.png"];
  expect(uploads).toEqual(expected);
  const creates = api.posts.filter(item => item.path === "/api/skill-extractions");
  expect(creates).toHaveLength(1); expect(creates[0].body.upload_ids).toEqual(expected);
  await page.reload(); await expect(page.getByText("任务状态：待确认")).toBeVisible();
  expect(api.posts.filter(item => item.path === "/api/skill-extractions")).toHaveLength(1);
});

test("batch picker rejects excess and invalid files without truncating existing selection", async ({ page }) => {
  await installControlledApi(page); await page.goto("/skills");
  await page.getByRole("button", { name: "从作品提炼", exact: true }).click();
  const input = page.getByLabel("参考图片", { exact: true });
  await input.setInputFiles(Array.from({ length: 20 }, (_, i) => ({ name: `${i}.png`, mimeType: "image/png", buffer: tinyPng })));
  await expect(page.getByLabel("已选图片").getByRole("img")).toHaveCount(20);
  await input.setInputFiles({ name: "extra.png", mimeType: "image/png", buffer: tinyPng });
  await expect(page.getByRole("alert")).toContainText("本次未添加");
  await input.setInputFiles({ name: "wrong.txt", mimeType: "text/plain", buffer: Buffer.from("wrong") });
  await expect(page.getByRole("alert")).toContainText("请选择 PNG");
  await input.setInputFiles({ name: "large.png", mimeType: "image/png", buffer: Buffer.alloc(4 * 1024 * 1024 + 1) });
  await expect(page.getByRole("alert")).toContainText("每张最多 4 MiB");
  await expect(page.getByLabel("已选图片").getByRole("img")).toHaveCount(20);
});

test("partial batch upload preserves files and resumes without reuploading completed images", async ({ page }) => {
  const api = await installControlledApi(page);
  const uploaded: string[] = []; let fail = true;
  await page.route("**/api/skill-extractions/uploads", async route => {
    const name = route.request().postDataJSON().name; uploaded.push(name);
    if (name === "second.png" && fail) { fail = false; await route.abort("failed"); return; }
    await route.fulfill({ json: { id: name, name, url: `/api/skill-extractions/uploads/${name}` } });
  });
  await page.goto("/skills"); await page.getByRole("button", { name: "从作品提炼", exact: true }).click();
  await page.getByLabel("参考图片", { exact: true }).setInputFiles(["first.png", "second.png"].map(name => ({ name, mimeType: "image/png", buffer: tinyPng })));
  await page.getByRole("button", { name: "开始提炼", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByLabel("已选图片").getByRole("img")).toHaveCount(2);
  expect(api.posts.filter(item => item.path === "/api/skill-extractions")).toHaveLength(0);
  await page.getByRole("button", { name: "开始提炼", exact: true }).click();
  await expect(page.getByText("任务状态：待确认")).toBeVisible();
  expect(uploaded).toEqual(["first.png", "second.png", "second.png"]);
  expect(api.posts.filter(item => item.path === "/api/skill-extractions")).toHaveLength(1);
});

test("pending batch upload locks selection and duplicate form submission", async ({ page }) => {
  const api = await installControlledApi(page);
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/skill-extractions/uploads", async route => {
    await gate;
    await route.fulfill({ json: { id: "locked.png", name: "locked.png", url: "/api/skill-extractions/uploads/locked.png" } });
  });
  await page.goto("/skills"); await page.getByRole("button", { name: "从作品提炼", exact: true }).click();
  await page.getByLabel("参考图片", { exact: true }).setInputFiles({ name: "locked.png", mimeType: "image/png", buffer: tinyPng });
  await page.getByRole("button", { name: "开始提炼", exact: true }).click();
  try {
    await expect(page.getByRole("button", { name: "正在上传 1 / 1…", exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "继续添加", exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "移除 locked.png", exact: true })).toBeDisabled();
    await page.getByLabel("参考图片", { exact: true }).evaluate(input => input.closest("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    expect(api.posts.filter(item => item.path === "/api/skill-extractions")).toHaveLength(0);
  } finally { release(); }
  await expect(page.getByText("任务状态：待确认")).toBeVisible();
  expect(api.posts.filter(item => item.path === "/api/skill-extractions")).toHaveLength(1);
});

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
  await expect(page.getByRole("button", { name: "作品完整 Skill", exact: true })).toBeVisible();
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

test("keeps diagnostic degradation visible after refresh without resubmitting", async ({ page }, testInfo) => {
  const api = await installControlledApi(page, "running");
  const warning = "本次进度或执行记录曾保存失败，部分展示可能不完整；不代表任务执行失败。";
  let running = true;
  await page.route("**/api/skill-extractions/extract-e2e", async route => {
    const body = fakeJob(running ? "running" : "ready", { observation_warning: warning, operation: running ? "extract" : null });
    await route.fulfill({ json: body });
  });
  await submitOneImage(page);
  await expect(page.getByText(warning, { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "取消当前操作" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "确认加入 Skill 库" })).toHaveCount(0);
  await page.reload();
  await expect(page.getByText(warning, { exact: true })).toBeVisible();
  expect(api.posts.filter(item => item.path === "/api/skill-extractions")).toHaveLength(1);
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`diagnostic-warning-${viewport.width}.png`), fullPage: true });
  }
  running = false;
  await page.reload();
  await expect(page.getByText("任务状态：待确认")).toBeVisible();
  await expect(page.getByText(warning, { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "确认加入 Skill 库" })).toBeEnabled();
  expect(api.posts.filter(item => item.path === "/api/skill-extractions")).toHaveLength(1);
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

test("shows public messages and failed tools, and expands the real full message on demand", async ({ page }) => {
  const api = await installControlledApi(page, "running");
  await submitOneImage(page);
  await expect(page.getByText("任务状态：进行中")).toBeVisible();
  await expect(page.getByText("Codex 进度消息")).toBeVisible();
  await expect(page.getByText("完整消息结尾标记")).toHaveCount(0);
  await expect(page.getByText(/reasoning.*secret/i)).toHaveCount(0);
  await page.getByRole("button", { name: "展开全文" }).click();
  await expect(page.getByText("完整消息结尾标记")).toBeVisible();
  await page.getByRole("button", { name: "收起全文" }).click();
  await expect(page.getByText("完整消息结尾标记")).toHaveCount(0);
  await page.getByRole("button", { name: "查看工具输入 / 结果" }).click();
  await expect(page.getByText("工具读取失败：权限不足")).toBeVisible();
  await expect(page.getByText("commandExecution")).toBeHidden();
  await page.getByText("技术标识").last().click();
  await expect(page.getByText("commandExecution")).toBeVisible();
  expect(api.posts.filter(item => item.path === "/api/skill-extractions")).toHaveLength(1);
  expect(api.posts.filter(item => item.path.endsWith("/uploads"))).toHaveLength(1);
});

test("refreshes a selected task's events, clears the prior stream and never resubmits", async ({ page }) => {
  const api = await installControlledApi(page, "running", false, true);
  await submitOneImage(page);
  await expect(page.getByText("任务状态：进行中")).toBeVisible();
  await expect(page.getByText("Codex 进度消息")).toBeVisible();
  await page.reload();
  await expect(page.getByText("任务状态：进行中")).toBeVisible();
  await expect(page.getByText("新 stream 消息")).toBeVisible();
  await expect(page.getByText("Codex 进度消息")).toHaveCount(0);
  expect(api.eventReads()).toBeGreaterThanOrEqual(2);
  expect(api.posts.filter(item => item.path === "/api/skill-extractions")).toHaveLength(1);
  expect(api.posts.filter(item => item.path.endsWith("/uploads"))).toHaveLength(1);
});

test("explains that older tasks may have no recorded public activity", async ({ page }) => {
  const api = await installControlledApi(page, "failed", false, false, true);
  await submitOneImage(page, "模拟失败");
  await expect(page.getByText("受控测试：Codex 暂不可用")).toBeVisible();
  await expect(page.getByText("此任务没有已记录的公开活动；旧任务可能未保存这类记录。")).toBeVisible();
  expect(api.posts.filter(item => item.path === "/api/skill-extractions")).toHaveLength(1);
});

test("refreshes a running tool's expanded result when the same event completes", async ({ page }) => {
  const api = await installControlledApi(page, "running", false, false, false, true);
  await submitOneImage(page);
  await expect(page.getByText("工具活动 · 进行中")).toBeVisible();
  await page.getByRole("button", { name: "查看工具输入 / 结果" }).click();
  await expect(page.getByText(/工具仍在运行/)).toBeVisible();
  await expect(page.getByText("工具活动 · 已完成")).toBeVisible({ timeout: 7000 });
  await expect(page.getByText("工具完成结果：草稿文件已读取完毕。")).toBeVisible();
  expect(api.eventReads()).toBeGreaterThanOrEqual(2);
  expect(api.posts.filter(item => item.path === "/api/skill-extractions")).toHaveLength(1);
});

test("does not restore the older-events button after all pages have been loaded", async ({ page }) => {
  const api = await installControlledApi(page, "running", false, false, false, false, true);
  await submitOneImage(page);
  await expect(page.getByText("活动 52", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "加载更早活动" }).click();
  await expect(page.getByText("活动 2", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "加载更早活动" }).click();
  await expect(page.getByText("活动 1", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "加载更早活动" })).toHaveCount(0);
  const readsBeforePoll = api.latestEventReads();
  await expect.poll(api.latestEventReads, { timeout: 5000 }).toBeGreaterThan(readsBeforePoll);
  await expect(page.getByRole("button", { name: "加载更早活动" })).toHaveCount(0);
});
