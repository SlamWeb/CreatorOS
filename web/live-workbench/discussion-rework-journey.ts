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

export async function imageInfo(image: Locator, request: APIRequestContext, timeout = 30_000): Promise<ImageInfo> {
  const deadline = Date.now() + timeout;
  await expect(image).toBeVisible({ timeout });
  await expect.poll(() => image.evaluate((element: HTMLImageElement) =>
    element.complete && element.naturalWidth > 0 && element.naturalHeight > 0), { timeout: Math.max(1, deadline - Date.now()) }).toBe(true);
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

export function sentDiscussionQueries(mutations: Array<{ method: string; path: string; body?: string }>, frozen: string[]) {
  const actual = mutations.filter(row => row.method === "POST" && /^\/api\/agent\/sessions\/[^/]+\/turns$/.test(row.path))
    .map(row => JSON.parse(row.body ?? "{}").text as unknown);
  if (actual.length > frozen.length || actual.some((query, index) => typeof query !== "string" || query !== frozen[index])) {
    throw new Error("实际发送的讨论问题必须是冻结问题的准确前缀；不允许补写未发送的 query 或重放。");
  }
  return actual as string[];
}

type DiscussionTurn = { turn_id: string; question: string };
type DiscussionRecord = { id: string; request_id: string; run_id: string; revision_id: string;
  artifact_digest: string; message: string; reply: string; status: string };
type DiscussionDelegation = { turn_id: string; call_id: string; request_snapshot_id: string;
  arguments: Omit<DiscussionRecord, "id" | "reply" | "status">; result: Omit<DiscussionRecord, "message" | "artifact_digest"> };

export async function readDiscussionDelegation(request: Pick<APIRequestContext, "get">, sessionId: string, turnId: string) {
  const base = `/api/agent/sessions/${encodeURIComponent(sessionId)}/turn-trace/${encodeURIComponent(turnId)}`;
  const indexResponse = await request.get(base, { maxRetries: 0 });
  expect(indexResponse.ok()).toBe(true);
  const index = await indexResponse.json();
  expect(index.turn_id).toBe(turnId);
  expect(index.available).toBe(true);
  expect(index.requests.length).toBeGreaterThan(0);
  const delegations: DiscussionDelegation[] = [];
  for (const step of index.requests) {
    expect(step.snapshot_available).toBe(true);
    const response = await request.get(`${base}/requests/${encodeURIComponent(step.request_id)}`, { maxRetries: 0 });
    expect(response.ok()).toBe(true);
    const snapshot = await response.json();
    expect(snapshot.turn_id).toBe(turnId);
    expect(snapshot.request_id).toBe(step.request_id);
    for (const call of snapshot.response?.tool_calls ?? []) {
      if (call.name !== "discuss_content_run") continue;
      const results = snapshot.tool_results.filter((row: { tool_call_id: string }) => row.tool_call_id === call.id);
      expect(results).toHaveLength(1);
      expect(results[0].name).toBe(call.name);
      expect(results[0].is_error).toBe(false);
      const args = typeof call.arguments === "string" ? JSON.parse(call.arguments) : call.arguments;
      const raw = results[0].raw_content;
      expect(raw).toBeDefined();
      const result = typeof raw === "string" ? JSON.parse(raw) : raw;
      delegations.push({ turn_id: turnId, call_id: call.id, request_snapshot_id: step.request_id,
        arguments: args, result });
    }
  }
  expect(delegations, "每个实际用户轮次恰有一条真实讨论调用").toHaveLength(1);
  return delegations[0];
}

export function assertDiscussionRecords(records: DiscussionRecord[], delegations: DiscussionDelegation[],
  target: { run_id: string; revision_id: string; artifact_digest: string }) {
  expect(records).toHaveLength(2);
  expect(delegations).toHaveLength(2);
  for (const key of ["turn_id", "call_id"] as const) expect(new Set(delegations.map(row => row[key])).size).toBe(2);
  for (const delegation of delegations) {
    for (const key of ["turn_id", "call_id", "request_snapshot_id"] as const) {
      expect(typeof delegation[key]).toBe("string");
      expect(delegation[key].trim()).not.toBe("");
    }
  }
  for (const key of ["id", "request_id"] as const) expect(new Set(records.map(row => row[key])).size).toBe(2);
  for (const [index, row] of records.entries()) {
    const { arguments: args, result } = delegations[index];
    for (const key of ["run_id", "revision_id", "artifact_digest", "request_id", "message"] as const) {
      expect(typeof args[key]).toBe("string");
      expect(args[key].trim()).not.toBe("");
    }
    // DiscussContentRunArgs uses str_strip_whitespace for every string field.
    expect({ run_id: args.run_id.trim(), revision_id: args.revision_id.trim(), artifact_digest: args.artifact_digest.trim() }).toEqual(target);
    expect({ run_id: row.run_id, revision_id: row.revision_id, artifact_digest: row.artifact_digest }).toEqual(target);
    expect(row.message).toBe(args.message.trim());
    expect(row.request_id).toBe(args.request_id.trim());
    for (const key of ["id", "request_id", "run_id", "revision_id", "status", "reply"] as const) {
      expect(row[key]).toBe(result[key]);
    }
    expect(row.id).toBeTruthy();
    expect(row.request_id).toBeTruthy();
    expect(row.status).toBe("completed");
    expect(row.reply.trim()).not.toBe("");
  }
}
type DiscussionSession = { id: string; status: string; error: string | null; has_older: boolean;
  entries: Array<{ kind: string; text?: string; turn_id?: string; complete?: boolean; terminal?: boolean }> };

export function discussionTranscript(session: DiscussionSession, turns: DiscussionTurn[]) {
  if (session.status !== "idle" || session.error !== null || session.has_older !== false) {
    throw new Error("讨论会话必须完整结束且公开记录未截断。");
  }
  if (!turns.length || new Set(turns.map(turn => turn.turn_id)).size !== turns.length
    || turns.some(turn => !turn.turn_id || !turn.question)) throw new Error("实际讨论 turn_id 必须非空且唯一。");
  const users = session.entries.filter(entry => entry.kind === "user")
    .map(entry => ({ turn_id: entry.turn_id, question: entry.text }));
  expect(users, "GET 用户轮次必须与实际 POST 身份、原文和顺序相同").toEqual(turns);
  const publicEntries = session.entries.filter(entry => entry.kind === "assistant" && entry.text);
  const finals = publicEntries.map((entry, public_index) => ({ ...entry, public_index }))
    .filter(entry => entry.complete === true || entry.terminal === true);
  if (finals.length !== turns.length || finals.some((entry, index) => entry.complete !== true
    || entry.terminal !== true || !entry.text?.trim() || entry.turn_id !== turns[index].turn_id)) {
    throw new Error("每条实际 POST 必须恰有一条 complete=true/terminal=true 最终回复，身份与顺序不可改变。");
  }
  return { public_bodies: publicEntries.map(entry => entry.text!),
    final_replies: finals.map(entry => ({ turn_id: entry.turn_id!, text: entry.text!, public_index: entry.public_index })) };
}

export async function readCompletedDiscussionTranscript(request: Pick<APIRequestContext, "get">,
  sessionId: string, turns: DiscussionTurn[], browser: Record<string, unknown>) {
  const session = await readTask(request, `/api/agent/sessions/${sessionId}`, browser) as unknown as DiscussionSession;
  expect(session.id).toBe(sessionId);
  return discussionTranscript(session, turns);
}

export function assertDiscussionRefresh(before: ReturnType<typeof discussionTranscript>,
  after: ReturnType<typeof discussionTranscript>, visibleBefore: string[], visibleAfter: string[]) {
  expect(after.public_bodies, "刷新必须保留全部公开正文，包括工具前说明").toEqual(before.public_bodies);
  expect(after.final_replies, "刷新必须保留两轮最终回复的身份、原文和顺序").toEqual(before.final_replies);
  expect(visibleAfter, "刷新必须完整保留所有可见正文").toEqual(visibleBefore);
}

export async function discussionJourney(page: Page, request: APIRequestContext, info: TestInfo, scenario: DiscussionScenario,
  browser: BrowserEvidence, reload: () => Promise<void>, endRefresh: () => void) {
  const questions = scenario.discussion_questions;
  if (!questions || questions.length !== 2) throw new Error("P06 must receive both frozen scenario questions; the browser does not derive query text.");
  const runId = scenario.business_run_id ?? scenario.seed_run_id ?? scenario.run_id;
  const seed = await (await request.get(`/api/runs/${runId}`)).json();
  const seedRevisionId = scenario.seed_revision_id ?? scenario.revision_id;
  const selectedRevision = seed.revisions.find((row: { id: string }) => row.id === seedRevisionId);
  expect(selectedRevision?.review_digest).toBeTruthy();
  const discussionTarget = { run_id: runId, revision_id: seedRevisionId!, artifact_digest: selectedRevision.review_digest };
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
  const postedTurns: DiscussionTurn[] = [];
  const delegations: DiscussionDelegation[] = [];
  for (let turn = 0; turn < questions.length; turn++) {
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
    const sentBody = sentResponse.request().postDataJSON();
    expect(typeof sentBody.request_id).toBe("string");
    expect(sentBody.text).toBe(questions[turn]);
    // /turns returns a Session, not a top-level turn_id; the submitted user entry binds request_id.
    const accepted = await sentResponse.json() as DiscussionSession;
    expect(accepted.id).toBe(browser.session_id);
    expect(accepted.entries.filter(entry => entry.kind === "user" && entry.turn_id === sentBody.request_id)
      .map(entry => ({ turn_id: entry.turn_id, question: entry.text })))
      .toEqual([{ turn_id: sentBody.request_id, question: questions[turn] }]);
    postedTurns.push({ turn_id: sentBody.request_id, question: questions[turn] });
    sessionPosts.push({ path: new URL(sentResponse.url()).pathname, status: sentResponse.status(),
      body: sentBody });
    const chatTurn: Record<string, unknown> = { ...postedTurns[turn], trace_visible: false };
    chatTurns.push(chatTurn);
    browser.chat_turns = chatTurns;
    try {
      await expect(panel.locator(".chat-user")).toHaveCount(postedTurns.length);
      await expect(panel.locator(".agent-composer [role=status]"))
        .toContainText(/可以继续对话|本次未完成|已中断/, { timeout: 240_000 });
    } finally {
      // Keep the actual visible response/error even if a subsequent state or GET check fails.
      chatTurn.visible_public_replies = await panel.locator(".chat-answer").allInnerTexts();
      browser.discussion_alerts = await panel.getByRole("alert").allTextContents();
    }
    await expect(panel.getByRole("alert")).toHaveCount(0);
    await expect(panel.locator(".agent-composer [role=status]")).toContainText("可以继续对话");
    const persisted = await readCompletedDiscussionTranscript(request, String(browser.session_id), postedTurns, browser);
    await expect(panel.locator(".chat-answer")).toHaveCount(persisted.public_bodies.length);
    const reply = panel.locator(".chat-reply").nth(persisted.final_replies[turn].public_index);
    await expect(reply.locator(".chat-reply-actions")).toHaveCount(1);
    const answer = await reply.locator(".chat-answer").innerText();
    expect(answer.trim()).not.toBe("");
    visibleReplies.push(answer);
    chatTurn.visible_reply = answer;
    await reply.getByRole("button", { name: "查看回复 Trace" }).click();
    const calls: string[] = [];
    toolSets.push(calls);
    Object.assign(chatTurn, { trace_opened: true, tool_calls: calls });
    browser.chat_turns = chatTurns;
    browser.trace_tool_calls = toolSets;
    await inspectVisibleTrace(page, calls, async () => {
      await page.screenshot({ path: info.outputPath(`p06-turn-${turn + 1}-trace.png`), fullPage: false });
    });
    Object.assign(chatTurn, { trace_visible: true, tool_calls: calls });
    delegations.push(await readDiscussionDelegation(request, String(browser.session_id), postedTurns[turn].turn_id));
    browser.discussion_delegations = delegations;
    browser.trace_tool_calls = toolSets;
    browser.steps.push(`原账号聊天第 ${turn + 1} 轮已真实发送；可见完整回复与该轮 Trace 工具结果`);
    expect(calls.some(name => name.includes("discuss_content_run")), "每轮明确委派均应真实调用讨论工具").toBe(true);
    await expect(panel.locator(".discussion-activity"), "不能把首轮旧活动卡冒充第二轮新结果").toHaveCount(turn + 1);
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
  expect(secondCalls.some(name => name.includes("discuss_content_run"))).toBe(true);
  const discussionLinks = discussionHrefs.map(href => new URL(href, page.url()));
  expect(discussionLinks).toHaveLength(2);
  const firstLink = discussionLinks[0];
  const latestLink = discussionLinks[1];
  const runIdFromLink = firstLink.pathname.split("/")[2];
  const revisionId = firstLink.searchParams.get("revision");
  expect(runIdFromLink).toBe(discussionTarget.run_id);
  expect(revisionId).toBe(discussionTarget.revision_id);
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
  const transcriptBeforeRefresh = await readCompletedDiscussionTranscript(request, String(browser.session_id), postedTurns, browser);
  expect(transcriptBeforeRefresh.final_replies).toHaveLength(2);
  await expect(panel.locator(".chat-answer")).toHaveCount(transcriptBeforeRefresh.public_bodies.length);
  const publicRepliesBeforeRefresh = await panel.locator(".chat-answer").allInnerTexts();
  browser.public_replies_before_refresh = publicRepliesBeforeRefresh;
  browser.public_reply_bodies_before_refresh = transcriptBeforeRefresh.public_bodies;
  browser.final_reply_entries_before_refresh = transcriptBeforeRefresh.final_replies;
  await page.screenshot({ path: info.outputPath("account-chat-two-discussion-rounds.png"), fullPage: false });
  const checkpoint = await request.post("/__live_eval__/checkpoint", { data: browser });
  expect(checkpoint.ok()).toBe(true);

  const beforeRefresh = Number(browser.posts_after_refresh ?? 0);
  await reload();
  await page.getByRole("button", { name: "打开账号对话" }).click();
  const transcriptAfterRefresh = await readCompletedDiscussionTranscript(request, String(browser.session_id), postedTurns, browser);
  await expect.poll(() => panel.locator(".chat-answer").allInnerTexts()).toEqual(publicRepliesBeforeRefresh);
  const publicRepliesAfterRefresh = await panel.locator(".chat-answer").allInnerTexts();
  assertDiscussionRefresh(transcriptBeforeRefresh, transcriptAfterRefresh, publicRepliesBeforeRefresh, publicRepliesAfterRefresh);
  for (let index = 0; index < visibleReplies.length; index++) {
    const reply = panel.locator(".chat-reply").nth(transcriptAfterRefresh.final_replies[index].public_index);
    await expect(reply.locator(".chat-reply-actions")).toHaveCount(1);
    await expect.poll(() => reply.locator(".chat-answer").innerText()).toBe(visibleReplies[index]);
  }
  const refreshedActivities = panel.locator(".discussion-activity");
  await expect(refreshedActivities).toHaveCount(2);
  const refreshedLinks = await refreshedActivities.getByRole("link", { name: "查看同一讨论 ↗" }).evaluateAll(
    links => links.map(link => (link as HTMLAnchorElement).href));
  expect(refreshedLinks.map(value => new URL(value).searchParams.get("discussion"))).toEqual(discussionIds);
  expect(Number(browser.posts_after_refresh ?? 0)).toBe(beforeRefresh);
  endRefresh();
  browser.refreshed_discussion_ids = refreshedLinks.map(value => new URL(value).searchParams.get("discussion"));
  browser.refreshed_replies = publicRepliesAfterRefresh;
  browser.refreshed_reply_bodies = transcriptAfterRefresh.public_bodies;
  browser.refreshed_final_reply_entries = transcriptAfterRefresh.final_replies;
  await page.screenshot({ path: info.outputPath("account-chat-after-refresh.png"), fullPage: false });
  browser.posts_after_refresh = Number(browser.posts_after_refresh ?? 0);
  browser.steps.push("刷新后同一聊天的两轮回复与讨论链接仍在，期间没有业务 POST；未重放会话或讨论");

  const discussionLink = refreshedActivities.last().getByRole("link", { name: "查看同一讨论 ↗" });
  await discussionLink.click();
  await expect(page).toHaveURL(url => url.pathname === `/runs/${runIdFromLink}`
    && url.searchParams.get("discussion") === discussionIds[1]
    && url.searchParams.get("revision") === revisionId);
  const records = page.getByRole("region", { name: /第 \d+ 版的 Codex 讨论/ });
  const historyResponse = await request.get(`/api/runs/${runId}/discussion`, { maxRetries: 0 });
  expect(historyResponse.ok()).toBe(true);
  const persistedRecords: DiscussionRecord[] = (await historyResponse.json()).items;
  assertDiscussionRecords(persistedRecords, delegations, discussionTarget);
  expect(persistedRecords.map(row => row.id)).toEqual(discussionIds);
  browser.persisted_discussion_records = persistedRecords;
  await expect(records.locator(".discussion-entry")).toHaveCount(2);
  const visibleDiscussionReplies: string[] = [];
  for (const row of persistedRecords) {
    const entry = records.locator(`#discussion-entry-${row.id}`);
    await expect(entry.locator(".discussion-message")).toHaveText(row.message);
    await expect(entry.locator(".discussion-status-completed")).toHaveCount(1);
    const phrase = row.reply.split(/[#*`\[\]\n]/).map(text => text.trim()).sort((a, b) => b.length - a.length)[0];
    expect(phrase).toBeTruthy();
    await expect(entry.locator(".discussion-reply")).toContainText(phrase);
    visibleDiscussionReplies.push(await entry.locator(".discussion-reply").innerText());
  }
  browser.visible_discussion_replies = visibleDiscussionReplies;
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
  await expect(page).toHaveURL(url => url.pathname === `/runs/${runId}`
    && url.searchParams.get("revision") === revisionId && url.searchParams.get("discussion") === discussionIds[1]);
  await expect(page.getByRole("region", { name: /第 \d+ 版的 Codex 讨论/ }).locator(".discussion-entry")).toHaveCount(2);
  const refreshedResponse = await request.get(`/api/runs/${runId}/discussion`, { maxRetries: 0 });
  expect(refreshedResponse.ok()).toBe(true);
  const refreshedRecords: DiscussionRecord[] = (await refreshedResponse.json()).items;
  expect(refreshedRecords).toEqual(persistedRecords);
  expect(await records.locator(".discussion-entry").evaluateAll(items => items.map(item => item.id.replace(/^discussion-entry-/, "")))).toEqual(discussionIds);
  for (const [index, row] of refreshedRecords.entries()) {
    await expect(records.locator(`#discussion-entry-${row.id} .discussion-message`)).toHaveText(row.message);
    await expect.poll(() => records.locator(`#discussion-entry-${row.id} .discussion-reply`).innerText()).toBe(visibleDiscussionReplies[index]);
  }
  await expect(records.locator(".discussion-status-completed")).toHaveCount(2);
  browser.refreshed_discussion_record_details = refreshedRecords;
  expect(Number(browser.posts_after_refresh ?? 0)).toBe(postReadCount);
  endRefresh();
  browser.run_id = runIdFromLink;
  browser.revision_id = revisionId;
  browser.refreshed_discussion_records = 2;
  browser.run_unchanged = true;
  browser.steps.push("从账号聊天的真实讨论入口打开原 Run；页面显示两条同 Revision 已完成记录，刷新后仍相同且原 Run 状态/版本/审批未变");
  browser.completed = true;
}
