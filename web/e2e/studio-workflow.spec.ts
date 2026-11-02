import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";

test("workspace first use to revision and approval survives refresh", async ({ page, request }, testInfo) => {
  test.setTimeout(120_000);
  const read = async (path: string) => {
    const response = await request.get(path);
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  };
  await page.route(/\/api\/creators\/[^/]+\/tasks(?:\?.*)?$/, route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ items: [{ id: "e2e-task", kind: "research", title: "Creator Routing 选题调研", status: "running", series_id: null, run_id: null, url: null, updated_at: "2026-10-08T08:00:00Z", last_activity_at: "2026-10-08T08:00:00Z" }], summary: { active: 1, awaiting_approval: 0, failed: 0 }, as_of: "2026-10-08T08:00:00Z" }),
  }));
  await page.goto("/");
  await expect(page.getByRole("link", { name: "进入工作台", exact: true })).toBeVisible();
  await page.getByRole("link", { name: "进入工作台", exact: true }).click();
  await expect(page).toHaveURL(/\/workspace$/);
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();

  await page.getByRole("button", { name: "切换账号", exact: true }).click();
  await page.getByRole("dialog", { name: "切换账号", exact: true }).getByRole("button", { name: "新建账号", exact: true }).click();
  await page.getByLabel("新账号名称").fill("E2E 知识实验室");
  await page.getByLabel("新账号标识").fill("e2e_lab");
  await page.getByRole("button", { name: "创建", exact: true }).click();
  await expect(page.getByRole("heading", { name: "E2E 知识实验室", level: 1 })).toBeVisible();
  const creatorId = new URL(page.url()).searchParams.get("creator")!;
  expect(creatorId).toBeTruthy();
  expect(await read(`/api/creators/${creatorId}`)).toMatchObject({ display_name: "E2E 知识实验室", account_handle: "e2e_lab" });
  await expect(page.getByText("这个账号还没有栏目", { exact: true })).toBeVisible();
  await page.getByRole("region", { name: "任务", exact: true }).getByRole("button", { name: "查看任务", exact: true }).click();
  await page.screenshot({ path: testInfo.outputPath("01-first-use.png"), fullPage: true });
  await expect(page.getByRole("region", { name: "任务" })).toContainText("Creator Routing 选题调研");
  await expect(page.getByRole("region", { name: "任务" })).toContainText("进行中 1");

  await page.getByRole("region", { name: "栏目", exact: true }).getByRole("button", { name: "新建栏目", exact: true }).click();
  await page.getByLabel("新栏目名称").fill("Agent 每日一题");
  await page.getByRole("button", { name: "创建", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Agent 每日一题", level: 1 })).toBeVisible();
  const seriesId = new URL(page.url()).searchParams.get("series")!;
  expect(await read(`/api/series/${seriesId}`)).toMatchObject({ creator_id: creatorId, name: "Agent 每日一题" });
  // New columns have no implicit production Skill. Set this controlled run's
  // explicit default through the real account/pool APIs, without mocking them.
  const library = await request.post(`/api/creators/${creatorId}/skills`, {
    data: { skill_id: "knowledge-to-carousel", request_id: crypto.randomUUID() },
  });
  expect(library.ok(), await library.text()).toBeTruthy();
  const pool = await read(`/api/series/${seriesId}/skills`);
  const configured = await request.put(`/api/series/${seriesId}/skills`, { data: {
    expected_version: pool.version, skill_ids: ["knowledge-to-carousel"],
    defaults: { image: [{ skill_id: "knowledge-to-carousel", role: "primary" }], video: [] },
  } });
  expect(configured.ok(), await configured.text()).toBeTruthy();

  await page.getByRole("button", { name: "添加选题", exact: true }).click();
  await page.getByLabel("新选题标题").fill("Agent State 和 Messages 有什么区别？");
  await page.getByRole("button", { name: "添加", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Agent State 和 Messages 有什么区别？" })).toBeVisible();
  const initialTopics = (await read(`/api/series/${seriesId}/topics`)).items;
  expect(initialTopics).toHaveLength(1);
  const topicId = initialTopics[0].id;
  expect(initialTopics[0]).toMatchObject({ title: "Agent State 和 Messages 有什么区别？", series_id: seriesId });

  // Preview 冲突覆盖保留：一条计划先被别处确认，抽屉内确认应提示过期而不是覆盖。
  const first = await page.evaluate(async (id) => {
    return fetch("/api/operations/preview", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        request_text: "再添加一条选题", series_id: id,
        plan: { schema_version: 1, operations: [{ action: "add_topics", series_id: id, topics: [{ topic_id: "e2e-extra-topic", title: "什么是 Context 压缩？", source: "manual" }] }] },
      }),
    }).then((response) => response.json());
  }, seriesId);
  await page.evaluate(async (id) => {
    const replacement = await fetch("/api/operations/preview", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        request_text: "模拟另一页面先修改队列", series_id: id,
        plan: { schema_version: 1, operations: [{ action: "add_topics", series_id: id, topics: [{ topic_id: "e2e-stale-winner", title: "另一条抢先写入", source: "manual" }] }] },
      }),
    }).then((response) => response.json());
    await fetch(`/api/operations/${replacement.id}/confirm`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_version: replacement.version, expected_revision: replacement.revision, confirmation_token: replacement.confirmation_token }),
    });
  }, seriesId);
  await page.goto(`/?series=${seriesId}&operation=${first.id}`);
  const dialog = page.getByRole("dialog", { name: "运营指令" });
  await expect(dialog.getByText("等待确认")).toBeVisible();
  await dialog.getByRole("button", { name: "确认写入队列" }).click();
  await expect(dialog.getByText(/计划已在其他页面更新|内容已变化|确认凭证已过期/)).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("02-stale-confirmation.png"), fullPage: true });
  const conflictTopics = (await read(`/api/series/${seriesId}/topics`)).items;
  expect(conflictTopics.map((topic: { id: string }) => topic.id).sort()).toEqual([topicId, "e2e-stale-winner"].sort());
  expect((await read(`/api/operations/${first.id}`)).status).not.toBe("succeeded");
  await page.keyboard.press("Escape");

  const topicCard = page.getByTestId(`library-${topicId}`);
  await topicCard.getByRole("button", { name: "制作", exact: true }).click();
  const topicDetail = page.getByRole("dialog", { name: "选题详情", exact: true });
  const production = topicDetail.getByRole("region", { name: "制作任务", exact: true });
  await production.getByRole("button", { name: "移除任务 2", exact: true }).click();
  await production.getByLabel("任务 1 名称", { exact: true }).fill("知识卡片");
  await production.getByLabel("任务 1 制作要求", { exact: true }).fill("制作三张图片，解释 Agent State 和 Messages 的区别。");
  await production.getByRole("group", { name: "任务 1 选择 Skill", exact: true })
    .getByRole("checkbox", { name: /知识点轮播/ }).check();
  const [submitted] = await Promise.all([
    page.waitForResponse(response => new URL(response.url()).pathname === `/api/topics/${topicId}/production-tasks`
      && response.request().method() === "POST"),
    production.getByRole("button", { name: "提交并开始制作（1）", exact: true }).click(),
  ]);
  expect(submitted.status()).toBe(202);
  const taskReceipt = await submitted.json();
  expect(taskReceipt.items).toHaveLength(1);
  expect(taskReceipt.topic_id).toBe(topicId);
  const discussionEntries: Record<string, unknown>[] = [];
  let discussionPostCount = 0;
  await page.route(/\/api\/runs\/[^/]+\/discussion$/, async route => {
    if (route.request().method() === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: discussionEntries }) });
      return;
    }
    discussionPostCount += 1;
    await route.fulfill({ status: 422, json: { error: { message: "普通讨论不应委派 Codex" } } });
  });
  await production.getByRole("link", { name: "查看任务", exact: true }).click();
  await expect(page.getByText(/Codex 正在生产/)).toBeVisible();
  await page.getByRole("button", { name: "讨论", exact: true }).click();
  const conversation = page.locator(".account-chat-panel");
  await expect(conversation).toBeVisible();
  await expect(conversation.getByLabel("给 Agent 的消息")).toHaveValue(/Run ID：/);
  await page.getByRole("button", { name: "关闭账号对话", exact: true }).click();
  let firstRevisionId = "";
  const firstRunId = new URL(page.url()).pathname.split("/").pop()!;
  expect(firstRunId).toBe(taskReceipt.items[0].run_id);
  const usedSkill = page.getByRole("region", { name: "本次运行使用的 Skill" });
  await expect(usedSkill).toBeVisible();
  await expect(usedSkill).not.toContainText("Run 冻结摘要");
  await expect(usedSkill).not.toContainText("编辑只影响新 Run");
  await usedSkill.getByRole("button", { name: "knowledge-to-carousel", exact: true }).click();
  const skillCard = page.getByRole("dialog", { name: "knowledge-to-carousel" });
  await expect(skillCard).toBeVisible();
  await skillCard.getByRole("button", { name: "关闭 Skill 详情" }).click();
  await page.screenshot({ path: testInfo.outputPath("03-producing.png"), fullPage: true });
  await expect(page.getByRole("button", { name: "批准第 1 版" })).toBeVisible({ timeout: 15_000 });
  firstRevisionId = await page.getByLabel("内容版本").inputValue();
  const firstRun = await read(`/api/runs/${firstRunId}`);
  expect(firstRun).toMatchObject({ topic_id: topicId, creator_id: creatorId, series_id: seriesId,
    status: "awaiting_approval", active_revision_number: 1 });
  expect(firstRun.revisions).toHaveLength(1);
  const originalRevision = firstRun.revisions[0];
  expect(originalRevision.id).toBe(firstRevisionId);
  expect(originalRevision.cards).toHaveLength(3);
  expect(originalRevision.artifact_available).toBe(true);
  expect(originalRevision.review_digest).toMatch(/^[a-f0-9]{64}$/);
  const originalImageChecksums: string[] = [];
  for (const card of originalRevision.cards) {
    const image = await request.get(card.url);
    expect(image.status()).toBe(200);
    expect(image.headers()["content-type"]).toContain("image/");
    originalImageChecksums.push(createHash("sha256").update(await image.body()).digest("hex"));
  }
  // Legacy saved discussions remain readable, but no longer offer a second composer.
  discussionEntries.push({
    id: "e2e-discussion-1", request_id: "legacy-fixture", run_id: firstRunId, revision_id: firstRevisionId,
    message: "请指出这张图里最需要核对的事实，不要修改产物。", status: "completed", reply: "已保存的历史核对结果。", error: null,
    thread_id: "legacy-discussion-thread", source_thread_id: "legacy-production-thread",
    created_at: "2026-10-08T08:10:00Z", updated_at: "2026-10-08T08:10:05Z",
    events: [{ id: 1, kind: "status", text: "讨论已开始", at: "2026-10-08T08:10:00Z" }, { id: 2, kind: "message", text: "已读取冻结产物。", at: "2026-10-08T08:10:02Z" }],
    context: { revision_number: 1, image_count: 3, images: [], skills: [], includes: ["第 1 版图片"], excludes: ["不修改产物"], history_mode: "forked_production" },
  });
  await page.getByRole("button", { name: "技术记录", exact: true }).click();
  await page.getByRole("button", { name: "旧讨论记录", exact: true }).click();
  const discussPanel = page.getByRole("region", { name: "第 1 版的 Codex 讨论" });
  await expect(discussPanel.getByText("已保存的历史核对结果。")).toBeVisible();
  await expect(discussPanel.getByLabel("想和 Codex 核对什么？")).toHaveCount(0);
  await discussPanel.getByText("本次传入什么").click();
  await expect(discussPanel.getByText("从生产对话分出独立讨论")).toBeVisible();
  await discussPanel.getByText("执行活动 · 2").click();
  await expect(discussPanel.getByText("已读取冻结产物。")).toBeVisible();
  expect(discussionPostCount).toBe(0);
  await page.screenshot({ path: testInfo.outputPath("discussion-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  const discussionWidth = await page.evaluate(() => ({ body: document.body.scrollWidth, viewport: window.innerWidth }));
  expect(discussionWidth.body).toBeLessThanOrEqual(discussionWidth.viewport);
  await page.screenshot({ path: testInfo.outputPath("discussion-mobile.png"), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 900 });

  await page.reload();
  const recoveredDiscussion = page.getByRole("region", { name: "第 1 版的 Codex 讨论" });
  await page.getByRole("button", { name: "技术记录", exact: true }).click();
  await page.getByRole("button", { name: "旧讨论记录", exact: true }).click();
  await expect(recoveredDiscussion.getByText("已保存的历史核对结果。")).toBeVisible();
  expect(discussionPostCount).toBe(0);

  await page.screenshot({ path: testInfo.outputPath("04-inspector.png"), fullPage: true });

  await page.getByRole("button", { name: "提出返工" }).click();
  await page.getByLabel("告诉生产者要改哪里").fill("第二张换成点餐场景，保留其余结构。");
  await page.getByRole("button", { name: "保存返工要求" }).click();
  await expect(page.getByRole("button", { name: "开始生产", exact: true })).toBeVisible();
  const requestedRevision = await read(`/api/runs/${firstRunId}`);
  expect(requestedRevision).toMatchObject({ status: "queued", active_revision_number: 2 });
  expect(requestedRevision.revisions).toHaveLength(2);
  expect(requestedRevision.revisions[0].artifact_digest).toBe(originalRevision.artifact_digest);
  expect(requestedRevision.revisions[1].attempts).toHaveLength(0);
  await page.getByRole("button", { name: "开始生产" }).click();
  await expect(page.getByRole("button", { name: "批准第 2 版" })).toBeVisible({ timeout: 15_000 });
  const revisedRun = await read(`/api/runs/${firstRunId}`);
  expect(revisedRun).toMatchObject({ status: "awaiting_approval", active_revision_number: 2 });
  expect(revisedRun.revisions).toHaveLength(2);
  expect(revisedRun.revisions[0].cards).toEqual(originalRevision.cards);
  expect(revisedRun.revisions.map((revision: { attempts: unknown[] }) => revision.attempts.length)).toEqual([1, 1]);
  for (const [index, card] of originalRevision.cards.entries()) {
    const image = await request.get(card.url);
    expect(image.status()).toBe(200);
    expect(createHash("sha256").update(await image.body()).digest("hex")).toBe(originalImageChecksums[index]);
  }
  const secondDiscussion = page.getByRole("region", { name: "第 2 版的 Codex 讨论" });
  await expect(secondDiscussion).toBeVisible();
  await expect(secondDiscussion.locator(".discussion-entry")).toHaveCount(0);
  await expect(secondDiscussion.getByLabel("想和 Codex 核对什么？")).toHaveCount(0);
  await expect(secondDiscussion.getByText("请指出这张图里最需要核对的事实，不要修改产物。")).toHaveCount(0);
  const runId = firstRunId;
  await page.evaluate(url => {
    window.history.pushState({}, "", url);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, `/runs/${runId}?revision=${encodeURIComponent(firstRevisionId)}&discussion=e2e-discussion-1`);
  await expect(page.getByLabel("内容版本")).toHaveValue(firstRevisionId);
  await expect(page.getByRole("region", { name: "第 1 版的 Codex 讨论" }).getByText("请指出这张图里最需要核对的事实，不要修改产物。", { exact: true })).toBeVisible();
  await page.goto(`/runs/${runId}`);
  await expect(page.getByRole("button", { name: "批准第 2 版" })).toBeVisible();
  await page.getByRole("button", { name: "批准第 2 版" }).click();
  await expect(page.getByText("已批准 · 尚未发布", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText("已批准 · 尚未发布", { exact: true })).toBeVisible();
  const approvedRun = await read(`/api/runs/${runId}`);
  expect(approvedRun.status).toBe("approved");
  expect(approvedRun.publication).toBeNull();
  expect(approvedRun.revisions[1].approved_at).toBeTruthy();
  await page.getByRole("button", { name: "发布", exact: true }).click();
  await page.getByRole("button", { name: "手动发布记录", exact: true }).click();

  // Isolated browser test: this link is deliberately not a real platform post.
  await expect(page.getByRole("link", { name: "下载已批准图片包" })).toBeVisible();
  await page.getByLabel("发布后粘贴笔记链接").fill("https://www.xiaohongshu.com/explore/e2e-isolated-example");
  await page.getByRole("button", { name: "登记为已发布" }).click();
  await expect(page.getByRole("link", { name: "打开笔记 ↗" })).toBeVisible();
  await page.getByLabel("阅读").fill("120");
  await page.getByLabel("点赞").fill("8");
  await page.getByRole("button", { name: "保存这次数据" }).click();
  await expect(page.getByText("历史回填 · 1 次")).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "发布", exact: true }).click();
  await page.getByRole("button", { name: "手动发布记录", exact: true }).click();
  await expect(page.getByRole("link", { name: "打开笔记 ↗" })).toBeVisible();
  await page.getByText("历史回填 · 1 次").click();
  await expect(page.getByText(/阅读 120 · 赞 8/)).toBeVisible();
  const publishedRun = await read(`/api/runs/${runId}`);
  expect(publishedRun.publication).toMatchObject({ post_url: "https://www.xiaohongshu.com/explore/e2e-isolated-example" });
  expect(publishedRun.publication.metrics).toHaveLength(1);
  expect(publishedRun.publication.metrics[0]).toMatchObject({ views: 120, likes: 8 });
  expect(publishedRun.revisions).toHaveLength(2);
  expect(publishedRun.revisions.map((revision: { attempts: unknown[] }) => revision.attempts.length)).toEqual([1, 1]);
  expect((await read(`/api/runs?creator_id=${creatorId}`)).items.map((run: { id: string }) => run.id)).toEqual([runId]);
  expect((await read(`/api/topics/${topicId}/production-tasks`)).items).toHaveLength(1);
  await page.screenshot({ path: testInfo.outputPath("approved-manual-publication.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  const publicationWidth = await page.evaluate(() => ({ body: document.body.scrollWidth, viewport: window.innerWidth }));
  expect(publicationWidth.body).toBeLessThanOrEqual(publicationWidth.viewport);
  await page.screenshot({ path: testInfo.outputPath("approved-manual-publication-mobile.png"), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 900 });

  await page.goto(`/workspace?creator=${creatorId}`);
  await expect(page.getByRole("complementary", { name: "账号与栏目" }).getByRole("button", { name: "Agent 每日一题" })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("05-workspace-with-data.png"), fullPage: true });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/workspace?creator=${creatorId}`);
  const mobileTree = page.getByRole("button", { name: /账号与栏目/ });
  await expect(mobileTree).toHaveAttribute("aria-expanded", "false");
  await mobileTree.click();
  await expect(page.locator(".workspace-rail")).toHaveClass(/is-open/);
  await expect(page.getByRole("complementary", { name: "账号与栏目" }).getByRole("button", { name: "Agent 每日一题" })).toBeVisible();
  const width = await page.evaluate(() => ({ body: document.body.scrollWidth, viewport: window.innerWidth }));
  expect(width.body).toBeLessThanOrEqual(width.viewport);
  await page.screenshot({ path: testInfo.outputPath("06-mobile-workspace.png"), fullPage: true });

  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    for (const route of ["/workspace", "/skills", "/agent"]) {
      await page.goto(route);
      await expect(page.locator("h1").first()).toBeVisible();
      await expect(page.locator(".studio-shell")).toHaveCSS("background-color", "rgb(255, 255, 255)");
      const overflow = await page.evaluate(() => [...document.querySelectorAll("main *")].filter(el => el.getBoundingClientRect().right > innerWidth + 1).map(el => ({ tag: el.tagName, cls: el.className, right: el.getBoundingClientRect().right })));
      expect(overflow, route).toEqual([]);
    }
  }
  await page.goto(`/workspace?creator=${creatorId}`);
  await page.getByRole("button", { name: "打开账号对话" }).click();
  const accountPanel = page.locator(".account-chat-panel");
  await expect(accountPanel).toBeVisible();
  await expect(accountPanel.getByRole("heading", { name: "E2E 知识实验室", exact: true })).toBeVisible();
  await expect(page).not.toHaveURL(/\/agent/);
  await page.getByRole("button", { name: "关闭账号对话" }).click();

  await page.goto("/agent");
  await page.getByRole("button", { name: "看看我的账号和栏目 ↗" }).click();
  await expect(page.getByLabel("给 Agent 的消息")).toHaveValue("看看我有哪些账号和栏目。");
  await page.getByRole("button", { name: /运营指令/ }).click();
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  // Failure injection verifies readable feedback without making a model call.
  await page.route(/\/api\/agent\/sessions(?:\?.*)?$/, route => route.fulfill({ status: 503, json: { error: { message: "隔离验收：服务暂不可用" } } }));
  await page.reload();
  await expect(page.getByRole("alert")).toContainText("隔离验收：服务暂不可用", { timeout: 15_000 });
  await page.screenshot({ path: testInfo.outputPath("agent-error-mobile.png"), fullPage: true });
});
