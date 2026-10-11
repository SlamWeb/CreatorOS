import { createHash } from "node:crypto";
import { expect, type APIRequestContext, type Locator, type Page, type TestInfo } from "@playwright/test";
import { readTask } from "./read-progress";

type DiscussionScenario = {
  creator_name: string; creator_id: string; series_id: string; series_name: string; run_id: string;
  query: string; discussion_questions?: string[]; rework_instruction?: string;
  eval_run_id?: string; seed_run_id?: string; seed_revision_id?: string; seed_image_sha256?: string;
  business_run_id?: string; revision_id?: string; image_sha256?: string;
};

type BrowserEvidence = Record<string, unknown> & { steps: string[] };
type ImageInfo = { src: string; width: number; height: number; sha256: string };

async function imageInfo(image: Locator, request: APIRequestContext): Promise<ImageInfo> {
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate((element: HTMLImageElement) =>
    element.complete && element.naturalWidth > 0 && element.naturalHeight > 0)).toBe(true);
  const rendered = await image.evaluate((element: HTMLImageElement) => ({
    src: element.currentSrc, width: element.naturalWidth, height: element.naturalHeight,
  }));
  const response = await request.get(rendered.src);
  expect(response.ok()).toBe(true);
  return { ...rendered, sha256: createHash("sha256").update(await response.body()).digest("hex") };
}

async function stage(request: APIRequestContext, runId: string, name: string, details: Record<string, unknown> = {}) {
  const response = await request.post("/__live_eval__/rework-stage", { data: { run_id: runId, name, ...details } });
  expect(response.ok(), `workbench stage ${name} must be accepted`).toBe(true);
}

export async function reworkJourney(page: Page, request: APIRequestContext, info: TestInfo,
  scenario: DiscussionScenario, browser: BrowserEvidence, reload: () => Promise<void>, endRefresh: () => void) {
  const runId = scenario.business_run_id ?? scenario.seed_run_id ?? scenario.run_id;
  const seedRevisionId = scenario.seed_revision_id ?? scenario.revision_id;
  const seedImageSha = scenario.seed_image_sha256 ?? scenario.image_sha256;
  expect(seedRevisionId).toBeTruthy();
  const before = await (await request.get(`/api/runs/${runId}`)).json();
  const oldRevision = before.revisions.find((row: { id: string }) => row.id === seedRevisionId);
  expect(oldRevision, "P04 must begin from the specified real seed revision").toBeTruthy();
  expect(oldRevision.cards).toHaveLength(1);
  const oldRevisionSnapshot = structuredClone(oldRevision);
  const route = `/runs/${runId}`;
  await page.goto(route);
  const initialImage = await imageInfo(page.getByRole("region", { name: "产物图片" }).locator(".carousel-stage img"), request);
  expect(initialImage.sha256).toBe(seedImageSha);
  await page.screenshot({ path: info.outputPath("rework-seed-revision.png"), fullPage: true });

  await page.getByRole("button", { name: "提出返工", exact: true }).click();
  const editor = page.getByRole("textbox", { name: "告诉生产者要改哪里", exact: true });
  await editor.fill(scenario.rework_instruction!);
  const savedResponse = page.waitForResponse(response => response.request().method() === "POST"
    && new URL(response.url()).pathname === `/api/runs/${runId}/revisions`);
  await page.getByRole("button", { name: "保存返工要求", exact: true }).click();
  const saved = await savedResponse;
  expect(saved.status()).toBe(201);
  const queued = await saved.json();
  const queuedRevision = queued.revisions.find((row: { revision_number: number }) => row.revision_number === oldRevision.revision_number + 1);
  expect(queued.revisions).toHaveLength(before.revisions.length + 1);
  expect(queuedRevision).toBeTruthy();
  expect(queuedRevision.attempts).toHaveLength(0);
  expect(queued.status).toBe("queued");
  expect(queuedRevision.instruction).toBe(scenario.rework_instruction);
  browser.rework_save = { path: new URL(saved.url()).pathname, status: saved.status(), body: saved.request().postDataJSON(),
    run_id: queued.id, revision_id: queuedRevision.id, revision_number: queuedRevision.revision_number };
  await page.getByRole("button", { name: /^生产记录/ }).click();
  await expect(page.locator(".revision-note")).toContainText(scenario.rework_instruction!);
  await expect(page.getByRole("button", { name: "开始生产", exact: true })).toBeEnabled();
  const afterSave = await (await request.get(`/api/runs/${runId}`)).json();
  expect(afterSave.revisions.find((row: { id: string }) => row.id === oldRevision.id)).toEqual(oldRevisionSnapshot);
  expect(afterSave.status).toBe("queued");
  expect(afterSave.revisions).toHaveLength(before.revisions.length + 1);
  const revisionSelect = page.getByRole("combobox", { name: "内容版本", exact: true });
  await expect(revisionSelect.locator("option")).toHaveCount(afterSave.revisions.length);
  await revisionSelect.selectOption(oldRevision.id);
  const savedHistoricalImage = await imageInfo(page.getByRole("region", { name: "产物图片" }).locator(".carousel-stage img"), request);
  expect(savedHistoricalImage.sha256).toBe(initialImage.sha256);
  await expect(page.getByText("正在查看历史版本，仅供对照，不能批准或修改。")).toBeVisible();
  await revisionSelect.selectOption(queuedRevision.id);
  const queuedImageRegion = page.getByRole("region", { name: "产物图片" });
  await expect(queuedImageRegion.locator(".carousel-stage img")).toHaveCount(0);
  await expect(queuedImageRegion).toContainText("还没有图片");
  await expect(page.getByRole("button", { name: "开始生产", exact: true })).toBeEnabled();
  const savedBeforeExecute: Record<string, unknown> = { run_status: afterSave.status, revision_count: afterSave.revisions.length,
    queued_revision_attempts: queuedRevision.attempts.length, old_revision: oldRevisionSnapshot,
    old_revision_image_sha256: savedHistoricalImage.sha256, queued_image_count: 0 };
  browser.saved_before_execute = savedBeforeExecute;
  const pausedPosts = Number(browser.posts_after_refresh ?? 0);
  await reload();
  await expect(page).toHaveURL(new RegExp(`${route}$`));
  await expect(revisionSelect).toHaveValue(queuedRevision.id);
  await expect(queuedImageRegion.locator(".carousel-stage img")).toHaveCount(0);
  expect(Number(browser.posts_after_refresh ?? 0)).toBe(pausedPosts);
  endRefresh();
  savedBeforeExecute.refreshed_queued_without_post = true;
  await stage(request, runId, "saved_before_execute", {
    business_run_id: runId, rework_request: browser.rework_save, explicit_execute_clicked: false,
    old_image: initialImage,
  });
  browser.steps.push("原 Run 页显式保存返工要求；API 与页面均确认新版排队、零 Attempt，旧版图像与记录未变；尚未执行");
  await page.screenshot({ path: info.outputPath("rework-saved-not-started.png"), fullPage: true });

  const executeResponse = page.waitForResponse(response => response.request().method() === "POST"
    && new URL(response.url()).pathname === `/api/runs/${runId}/execute`);
  await page.getByRole("button", { name: "开始生产", exact: true }).click();
  const execute = await executeResponse;
  expect(execute.status()).toBe(202);
  browser.execute_request = { path: new URL(execute.url()).pathname, status: execute.status(), body: execute.request().postDataJSON() };
  let final = queued;
  const terminalRunStatuses = /^(awaiting_approval|approved|interrupted|failed|cancelled)$/;
  await expect.poll(async () => {
    final = await readTask(request, `/api/runs/${runId}`, browser);
    return final.status;
  }, { timeout: 1_860_000, intervals: [1000, 3000, 5000] }).toMatch(terminalRunStatuses);
  expect(final.status).toBe("awaiting_approval");
  const newRevision = final.revisions.find((row: { id: string }) => row.id === queuedRevision.id);
  const preservedOld = final.revisions.find((row: { id: string }) => row.id === oldRevision.id);
  expect(newRevision).toBeTruthy();
  expect(newRevision.attempts).toHaveLength(1);
  expect(newRevision.cards).toHaveLength(1);
  expect(preservedOld).toEqual(oldRevisionSnapshot);
  await expect.poll(() => revisionSelect.locator("option").count()).toBe(2);
  await expect(page.locator(".run-workbench .run-title-row .status-pill")).toContainText("待批准");
  const newImage = await imageInfo(page.getByRole("region", { name: "产物图片" }).locator(".carousel-stage img"), request);
  expect(newImage.sha256).not.toBe(initialImage.sha256);
  await expect(revisionSelect).toHaveValue(newRevision.id);
  browser.new_revision = { id: newRevision.id, revision_number: newRevision.revision_number,
    attempt_status: newRevision.attempts[0].status, attempt_count: newRevision.attempts.length, image: newImage };

  await revisionSelect.selectOption(oldRevision.id);
  const historicalImage = await imageInfo(page.getByRole("region", { name: "产物图片" }).locator(".carousel-stage img"), request);
  expect(historicalImage.sha256).toBe(initialImage.sha256);
  await expect(page.getByText("正在查看历史版本，仅供对照，不能批准或修改。")).toBeVisible();
  await revisionSelect.selectOption(newRevision.id);
  const currentImage = await imageInfo(page.getByRole("region", { name: "产物图片" }).locator(".carousel-stage img"), request);
  expect(currentImage.sha256).toBe(newImage.sha256);
  await page.screenshot({ path: info.outputPath("rework-new-revision.png"), fullPage: true });
  browser.old_revision_image = historicalImage;
  browser.revision_comparison_visible = true;
  browser.steps.push("用户明确点击新版执行；同一 Run 生成一张新图，切换旧/新版本并按文件 SHA 验证旧图保留、新图独立，未批准/发布");
  await stage(request, runId, "completed", {
    business_run_id: runId, explicit_execute_clicked: true, old_revision_unchanged: true,
    old_image: historicalImage, new_image: newImage, unapproved: !final.publication && final.status === "awaiting_approval",
  });
  const postsBeforeReload = Number(browser.posts_after_refresh ?? 0);
  await reload();
  await expect(page).toHaveURL(new RegExp(`${route}$`));
  const afterReload = await imageInfo(page.getByRole("region", { name: "产物图片" }).locator(".carousel-stage img"), request);
  expect(afterReload.sha256).toBe(newImage.sha256);
  expect(Number(browser.posts_after_refresh ?? 0)).toBe(postsBeforeReload);
  endRefresh();
  browser.refreshed_image = afterReload;
  browser.refreshed_without_business_post = true;
  browser.completed = true;
}

async function inspectVisibleTrace(page: Page, names: string[], capture: () => Promise<void>) {
  const dialog = page.getByRole("dialog", { name: "回复 Trace" });
  const steps = dialog.getByRole("navigation", { name: "模型请求步骤" }).getByRole("button");
  await expect(steps.first()).toBeVisible();
  for (let index = 0; index < await steps.count(); index++) {
    await steps.nth(index).click();
    await expect.poll(async () => await dialog.locator(".chat-trace-snapshot").count()
      + await dialog.locator(".chat-trace-step-detail .chat-trace-state").count()).toBeGreaterThan(0);
    const tools = dialog.getByRole("tab", { name: "工具", exact: true });
    if (!(await tools.count())) continue;
    await tools.click();
    const calls = await dialog.locator(".chat-trace-tool-pair h5").allTextContents();
    for (const call of calls) names.push(call.split(" · call ID")[0]);
  }
  await capture();
  await expect(dialog.getByRole("alert")).toHaveCount(0);
  await page.getByRole("button", { name: "关闭 Trace" }).click();
}

export async function discussionJourney(page: Page, request: APIRequestContext, info: TestInfo, scenario: DiscussionScenario,
  browser: BrowserEvidence, reload: () => Promise<void>, endRefresh: () => void) {
  const questions = scenario.discussion_questions;
  if (!questions || questions.length !== 2) throw new Error("P06 must receive both frozen scenario questions; the browser does not derive query text.");
  const runId = scenario.business_run_id ?? scenario.seed_run_id ?? scenario.run_id;
  const seed = await (await request.get(`/api/runs/${runId}`)).json();
  const originalRun = { id: seed.id, status: seed.status, version: seed.version, active_revision_number: seed.active_revision_number,
    publication: seed.publication, revisions: seed.revisions };
  await page.goto("/");
  await page.getByRole("button", { name: `查看账号 ${scenario.creator_name}` }).click();
  await page.getByRole("button", { name: "打开账号对话" }).click();
  const panel = page.locator(".account-chat-panel");
  const sessions = await page.getByRole("button", { name: "关闭账号对话" }).count();
  expect(sessions).toBe(1);
  const sessionPosts: unknown[] = [];
  const chatTurns: Array<Record<string, unknown>> = [];
  const toolSets: string[][] = [];
  const visibleReplies: string[] = [];
  const discussionHrefs: string[] = [];
  for (let turn = 0; turn < questions.length; turn++) {
    const previousAnswerCount = await panel.locator(".chat-answer").count();
    await panel.getByRole("textbox", { name: "给 Agent 的消息" }).fill(questions[turn]);
    const created = turn === 0 ? page.waitForResponse(response => response.request().method() === "POST"
      && new URL(response.url()).pathname === "/api/agent/sessions") : null;
    const sent = page.waitForResponse(response => response.request().method() === "POST"
      && new URL(response.url()).pathname.endsWith("/turns"));
    await panel.getByRole("button", { name: "发送 ↑" }).click();
    if (created) {
      const response = await created;
      expect(response.status()).toBe(201);
      browser.session_id = (await response.json()).id;
      sessionPosts.push({ path: new URL(response.url()).pathname, status: response.status() });
    }
    const sentResponse = await sent;
    expect(sentResponse.status()).toBe(202);
    sessionPosts.push({ path: new URL(sentResponse.url()).pathname, status: sentResponse.status(),
      body: sentResponse.request().postDataJSON() });
    await expect.poll(() => panel.locator(".chat-answer").count(), { timeout: 240_000 }).toBe(previousAnswerCount + 1);
    await expect(panel.locator(".agent-composer [role=status]")).toContainText("可以继续对话", { timeout: 240_000 });
    await expect(panel.getByRole("alert")).toHaveCount(0);
    const answer = await panel.locator(".chat-answer").last().innerText();
    expect(answer.trim()).not.toBe("");
    visibleReplies.push(answer);
    const chatTurn: Record<string, unknown> = { question: questions[turn], visible_reply: answer, trace_visible: false };
    chatTurns.push(chatTurn);
    browser.chat_turns = chatTurns;
    await panel.getByRole("button", { name: "查看回复 Trace" }).last().click();
    const calls: string[] = [];
    toolSets.push(calls);
    Object.assign(chatTurn, { trace_opened: true, tool_calls: calls });
    browser.chat_turns = chatTurns;
    browser.trace_tool_calls = toolSets;
    await inspectVisibleTrace(page, calls, async () => {
      await page.screenshot({ path: info.outputPath(`p06-turn-${turn + 1}-trace.png`), fullPage: false });
    });
    Object.assign(chatTurn, { trace_visible: true, tool_calls: calls });
    browser.trace_tool_calls = toolSets;
    browser.steps.push(`原账号聊天第 ${turn + 1} 轮已真实发送；可见完整回复与该轮 Trace 工具结果`);
    const activity = panel.locator(".discussion-activity").last();
    await expect(activity).toContainText("讨论已完成", { timeout: 240_000 });
    const link = activity.getByRole("link", { name: "查看同一讨论 ↗" });
    await expect(link).toBeVisible();
    const href = (await link.getAttribute("href"))!;
    discussionHrefs.push(href);
    const discussionUrl = new URL(href, page.url());
    Object.assign(chatTurns[turn], { run_id: discussionUrl.pathname.split("/")[2],
      revision_id: discussionUrl.searchParams.get("revision"), discussion_id: discussionUrl.searchParams.get("discussion"),
      discussion_status: "completed" });
  }
  const firstCalls = toolSets[0];
  const secondCalls = toolSets[1];
  expect(firstCalls.some(name => name.includes("get_content_run"))).toBe(true);
  expect(firstCalls.some(name => name.includes("discuss_content_run"))).toBe(true);
  expect(secondCalls.some(name => name.includes("get_content_discussion"))).toBe(true);
  expect(secondCalls.some(name => name.includes("discuss_content_run"))).toBe(true);
  const discussionLinks = discussionHrefs.map(href => new URL(href, page.url()));
  expect(discussionLinks).toHaveLength(2);
  const firstLink = discussionLinks[0];
  const latestLink = discussionLinks[1];
  const runIdFromLink = firstLink.pathname.split("/")[2];
  const revisionId = firstLink.searchParams.get("revision");
  const discussionIds = discussionLinks.map(link => link.searchParams.get("discussion"));
  expect(discussionIds.every(Boolean)).toBe(true);
  expect(new Set(discussionIds).size).toBe(2);
  expect(latestLink.pathname.split("/")[2]).toBe(runIdFromLink);
  expect(latestLink.searchParams.get("revision")).toBe(revisionId);
  browser.first_discussion_link = { href: firstLink.pathname + firstLink.search, run_id: runIdFromLink,
    discussion_id: discussionIds[0], revision_id: revisionId };
  browser.session_posts_observed = sessionPosts;
  browser.chat_turns = chatTurns;
  browser.discussion_ids = discussionIds;
  browser.trace_tool_calls = toolSets;
  await page.screenshot({ path: info.outputPath("account-chat-two-discussion-rounds.png"), fullPage: false });
  const checkpoint = await request.post("/__live_eval__/checkpoint", { data: browser });
  expect(checkpoint.ok()).toBe(true);

  const beforeRefresh = Number(browser.posts_after_refresh ?? 0);
  await reload();
  await page.getByRole("button", { name: "打开账号对话" }).click();
  await expect.poll(() => panel.locator(".chat-answer").count()).toBe(2);
  for (let index = 0; index < visibleReplies.length; index++) {
    await expect.poll(() => panel.locator(".chat-answer").nth(index).innerText()).toBe(visibleReplies[index]);
  }
  const refreshedActivities = panel.locator(".discussion-activity");
  await expect(refreshedActivities).toHaveCount(2);
  const refreshedLinks = await refreshedActivities.getByRole("link", { name: "查看同一讨论 ↗" }).evaluateAll(
    links => links.map(link => (link as HTMLAnchorElement).href));
  expect(refreshedLinks.map(value => new URL(value).searchParams.get("discussion"))).toEqual(discussionIds);
  expect(Number(browser.posts_after_refresh ?? 0)).toBe(beforeRefresh);
  endRefresh();
  browser.refreshed_discussion_ids = refreshedLinks.map(value => new URL(value).searchParams.get("discussion"));
  browser.refreshed_replies = await panel.locator(".chat-answer").allTextContents();
  await page.screenshot({ path: info.outputPath("account-chat-after-refresh.png"), fullPage: false });
  browser.posts_after_refresh = Number(browser.posts_after_refresh ?? 0);
  browser.steps.push("刷新后同一聊天的两轮回复与讨论链接仍在，期间没有业务 POST；未重放会话或讨论");

  const discussionLink = refreshedActivities.last().getByRole("link", { name: "查看同一讨论 ↗" });
  await discussionLink.click();
  await expect(page).toHaveURL(url => url.pathname === `/runs/${runIdFromLink}`
    && url.searchParams.get("discussion") === discussionIds[1]
    && url.searchParams.get("revision") === revisionId);
  const records = page.getByRole("region", { name: /第 \d+ 版的 Codex 讨论/ });
  await expect(records.locator(".discussion-entry")).toHaveCount(2);
  await expect(records.locator(".discussion-entry").nth(0).locator(".discussion-message")).toHaveText(questions[0]);
  await expect(records.locator(".discussion-entry").nth(1).locator(".discussion-message")).toHaveText(questions[1]);
  await expect(records.locator(".discussion-entry .discussion-status-completed")).toHaveCount(2);
  const visibleRecordIds = await records.locator(".discussion-entry").evaluateAll(items => items.map(item =>
    (item as HTMLElement).id.replace(/^discussion-entry-/, "")));
  expect(visibleRecordIds).toEqual(discussionIds);
  const afterDiscussionRead = await (await request.get(`/api/runs/${runIdFromLink}`)).json();
  expect({ id: afterDiscussionRead.id, status: afterDiscussionRead.status, version: afterDiscussionRead.version,
    active_revision_number: afterDiscussionRead.active_revision_number, publication: afterDiscussionRead.publication,
    revisions: afterDiscussionRead.revisions }).toEqual(originalRun);
  await page.screenshot({ path: info.outputPath("run-discussion-two-records.png"), fullPage: false });
  const postReadCount = Number(browser.posts_after_refresh ?? 0);
  await reload();
  await expect(page).toHaveURL(url => url.searchParams.get("discussion") === discussionIds[1]);
  await expect(page.getByRole("region", { name: /第 \d+ 版的 Codex 讨论/ }).locator(".discussion-entry")).toHaveCount(2);
  expect(Number(browser.posts_after_refresh ?? 0)).toBe(postReadCount);
  endRefresh();
  browser.run_id = runIdFromLink;
  browser.revision_id = revisionId;
  browser.refreshed_discussion_records = 2;
  browser.run_unchanged = true;
  browser.steps.push("从账号聊天的真实讨论入口打开原 Run；页面显示两条同 Revision 已完成记录，刷新后仍相同且原 Run 状态/版本/审批未变");
  browser.completed = true;
}
