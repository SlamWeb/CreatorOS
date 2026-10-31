// Opt-in paid benchmark driver. Only UI submits user turns; no routes are mocked.
// node web/e2e/live-benchmark-v2.mjs <isolated-base-url> <case-evidence-dir>
import { chromium, expect } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';

const [baseArgument, evidenceArgument] = process.argv.slice(2);
if (!baseArgument || !evidenceArgument) throw new Error('Expected isolated base URL and case evidence directory');
const base = new URL(baseArgument).origin;
if (!['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname))
  throw new Error('Benchmark URL must be a local isolated server');
const output = path.resolve(evidenceArgument);
mkdirSync(output, { recursive: true });
writeFileSync(path.join(output, 'browser-driver.started.json'), JSON.stringify({ base, started_at: new Date().toISOString() }), { flag: 'wx' });
const report = { base, completed: false, refresh_ok: false, session_id: null, turns: [], turn_posts: [], events: [], uploads: [], errors: [], writes: [], screenshots: [] };
const save = () => writeFileSync(path.join(output, 'browser-report.json'), JSON.stringify(report, null, 2));
const check = (condition, message) => { if (!condition) throw new Error(message); };
const errorRecord = (kind, error) => ({ kind, message: String(error?.stack ?? error), at: new Date().toISOString() });
let browser, context, page, scenario, variables = {}, timeoutMs = 300_000;
let firstFinishAttempted = false;

async function readJson(response, description) {
  if (!response.ok()) throw new Error(`${description}: HTTP ${response.status()} ${(await response.text()).slice(0, 1200)}`);
  return response.json();
}
async function screenshot(name) {
  const file = path.join(output, name);
  await page.screenshot({ path: file, fullPage: true });
  report.screenshots.push(file);
}
async function panel() {
  const root = page.locator('.account-chat-panel');
  if (!await root.isVisible()) await page.getByRole('button', { name: '打开账号对话', exact: true }).click();
  await root.waitFor({ state: 'visible', timeout: 30_000 });
  await root.getByRole('textbox', { name: '给 Agent 的消息', exact: true }).waitFor({ state: 'visible' });
  return root;
}
function expand(text) {
  return String(text).replace(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g, (_, name) => {
    check(Object.hasOwn(variables, name), `Missing scenario variable: ${name}`);
    return String(variables[name]);
  });
}
async function documentView() {
  check(report.session_id, 'No real session receipt has been observed');
  const doc = await readJson(await context.request.get(`${base}/api/agent/sessions/${encodeURIComponent(report.session_id)}`), 'Read session');
  check(doc.id === report.session_id, 'Session read changed its identity');
  check(doc.creator_id === scenario.creator_id, 'Session belongs to another creator');
  return doc;
}
async function waitForIdle() {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const doc = await documentView();
    if (doc.status !== 'running') return doc;
    await page.waitForTimeout(500);
  }
  throw new Error(`Session remained running past ${timeoutMs / 1000}s`);
}
async function beginFirstTextObserver(root) {
  await root.evaluate(element => {
    window.__benchmarkText?.observer?.disconnect();
    const existing = new Set(element.querySelectorAll('.chat-answer'));
    const state = { started: performance.now(), latency_ms: null, reason: 'No new nonempty .chat-answer observed', observer: null };
    const inspect = () => {
      if (state.latency_ms !== null) return;
      const answer = [...element.querySelectorAll('.chat-answer')].find(node => !existing.has(node) && node.textContent?.trim());
      if (answer) { state.latency_ms = performance.now() - state.started; state.reason = null; state.observer.disconnect(); }
    };
    state.observer = new MutationObserver(inspect);
    state.observer.observe(element, { subtree: true, childList: true, characterData: true });
    window.__benchmarkText = state;
  });
}
async function uploadReference(root, alias) {
  check(alias === 'reference', `Unsupported attachment alias: ${alias}`);
  const file = scenario.attachment_files?.[alias];
  check(typeof file === 'string' && path.isAbsolute(file), 'Scenario reference must be an isolated absolute file path');
  await expect(root.locator('input[aria-label="选择图片文件"]')).toBeEnabled({ timeout: 30_000 });
  const responsePromise = page.waitForResponse(response => {
    const url = new URL(response.url());
    return url.origin === base && /^\/api\/agent\/sessions\/[^/]+\/attachments$/.test(url.pathname) && response.request().method() === 'POST';
  }, { timeout: 30_000 });
  const [response] = await Promise.all([responsePromise, root.locator('input[aria-label="选择图片文件"]').setInputFiles(file)]);
  const receipt = await readJson(response, 'Reference upload');
  const sid = new URL(response.url()).pathname.split('/')[4];
  check(typeof receipt.id === 'string' && receipt.id.length > 0, 'Upload receipt has no attachment ID');
  if (report.session_id) check(report.session_id === sid, 'Upload changed account session');
  report.session_id = sid;
  variables.attachment_a = receipt.id;
  report.uploads.push({ alias, session_id: sid, attachment_id: receipt.id, receipt });
  await root.getByRole('button', { name: `移除图片 ${receipt.name}`, exact: true }).waitFor({ state: 'visible' });
  save();
}
async function userTurn(step, index) {
  const root = await panel();
  if (report.session_id) await waitForIdle();
  for (const alias of step.attachments ?? []) await uploadReference(root, alias);
  const query = expand(step.text);
  const before = report.session_id ? await documentView() : { entries: [] };
  const knownEntries = new Set((before.entries ?? []).map(entry => entry.id).filter(Boolean));
  const beforeAnswerCount = await root.locator('.chat-answer').count();
  const textBox = root.getByRole('textbox', { name: '给 Agent 的消息', exact: true });
  await textBox.fill(query);
  await beginFirstTextObserver(root);
  const started = Date.now();
  const responsePromise = page.waitForResponse(response => {
    const url = new URL(response.url());
    return url.origin === base && /^\/api\/agent\/sessions\/[^/]+\/turns$/.test(url.pathname) && response.request().method() === 'POST';
  }, { timeout: 30_000 });
  const [response] = await Promise.all([responsePromise, root.getByRole('button', { name: '发送 ↑', exact: true }).click()]);
  const payload = response.request().postDataJSON();
  const sid = new URL(response.url()).pathname.split('/')[4];
  const accepted = await readJson(response, 'User turn submission');
  check(accepted.id === sid, 'Turn response and URL session identities differ');
  if (report.session_id) check(report.session_id === sid, 'User turn changed session');
  report.session_id = sid;
  check(payload.text === query, 'Submitted UI text differs from scenario query');
  const turnId = payload.request_id;
  check(typeof turnId === 'string', 'Real turn request has no request_id');
  const post = { session_id: sid, turn_id: turnId, status: response.status(), request: payload };
  report.turn_posts.push(post);
  const turn = { index, query, session_id: sid, turn_id: turnId, accepted, completed: false };
  report.turns.push(turn);
  save();
  const doc = await waitForIdle();
  const entries = (doc.entries ?? []).filter(entry => entry.turn_id === turnId || (entry.id && !knownEntries.has(entry.id)));
  const answers = entries.filter(entry => entry.kind === 'assistant' && entry.text?.trim());
  turn.response = answers.map(entry => entry.text).join('\n\n');
  turn.entries = entries;
  turn.newcalls = entries.filter(entry => entry.kind === 'tool');
  turn.tool_arguments_source = 'Public entries may omit arguments; server raw Trace is authoritative';
  turn.session_status = doc.status;
  turn.session_error = doc.error;
  turn.elapsed_ms = Date.now() - started;
  check(answers.length > 0, `No public final answer for turn ${turnId}; session=${doc.status}`);
  await root.locator('.chat-answer').nth(beforeAnswerCount).waitFor({ state: 'visible', timeout: 30_000 });
  const finalReply = root.locator('.chat-reply').last();
  await finalReply.getByRole('button', { name: '查看回复 Trace', exact: true }).waitFor({ state: 'visible', timeout: 30_000 });
  const finalTraceResponse = page.waitForResponse(response => new URL(response.url()).pathname === `/api/agent/sessions/${sid}/turn-trace/${turnId}` && response.request().method() === 'GET', { timeout: 30_000 });
  const [traceResponse] = await Promise.all([finalTraceResponse, finalReply.getByRole('button', { name: '查看回复 Trace', exact: true }).click()]);
  turn.visible_reply_trace = await readJson(traceResponse, 'Visible final reply Trace');
  check(turn.visible_reply_trace.turn_id === turnId, 'Visible reply belongs to another turn');
  await page.getByRole('dialog', { name: '回复 Trace', exact: true }).getByRole('button', { name: '关闭 Trace', exact: true }).click();
  turn.rendered_final_reply = await finalReply.locator('.chat-answer').innerText();
  const timing = await page.evaluate(() => {
    const observed = window.__benchmarkText;
    observed?.observer?.disconnect();
    return { latency_ms: observed?.latency_ms ?? null, reason: observed?.reason ?? 'Observer unavailable' };
  });
  turn.browser_first_text_latency_ms = timing.latency_ms;
  turn.browser_first_text_latency_missing_reason = timing.reason;
  turn.final_reply_visible = true;
  turn.ui_elapsed_ms = Date.now() - started;
  turn.completed = true;
  await screenshot(`turn-${String(index + 1).padStart(2, '0')}.png`);
  writeFileSync(path.join(output, `turn-${String(index + 1).padStart(2, '0')}.json`), JSON.stringify(turn, null, 2));
  save();
}
async function hostEvent(step, index) {
  check(report.session_id, 'Host event requires a real UI session');
  await waitForIdle();
  const response = await context.request.post(`${base}/__benchmark__/event`, { data: { name: step.name, session_id: report.session_id }, timeout: timeoutMs });
  const receipt = await readJson(response, `Host event ${step.name}`);
  if (receipt.variables && typeof receipt.variables === 'object') Object.assign(variables, receipt.variables);
  report.events.push({ index, name: step.name, session_id: report.session_id, receipt });
  const expectedSession = report.session_id;
  const reopenedResponse = page.waitForResponse(response => new URL(response.url()).pathname === `/api/agent/sessions/${expectedSession}` && response.request().method() === 'GET', { timeout: 30_000 })
    .then(value => ({ value }), error => ({ error }));
  await page.reload({ waitUntil: 'domcontentloaded' });
  const root = await panel();
  const reopenedResult = await reopenedResponse;
  if (reopenedResult.error) throw reopenedResult.error;
  const reopened = await readJson(reopenedResult.value, 'Reopened account session');
  check(reopened.id === expectedSession && reopened.creator_id === scenario.creator_id, 'Reload lost the account conversation');
  const lastQuery = report.turns.at(-1)?.query;
  if (lastQuery) {
    check(reopened.entries?.some(entry => entry.kind === 'user' && entry.text === lastQuery), 'Reopened session lost prior user turn');
    await root.getByText(lastQuery, { exact: true }).last().waitFor({ state: 'visible', timeout: 30_000 });
  }
  report.events.at(-1).same_session_after_reload = true;
  await screenshot(`event-${String(index + 1).padStart(2, '0')}.png`);
  save();
}

async function finalRefresh() {
  const before = await waitForIdle();
  const expectedSession = report.session_id;
  const user = [...(before.entries ?? [])].reverse().find(entry => entry.kind === 'user');
  const assistant = [...(before.entries ?? [])].reverse().find(entry => entry.kind === 'assistant' && entry.text?.trim());
  check(user?.turn_id && assistant?.turn_id, 'Final refresh requires server user and assistant turn IDs');
  const turnPostCount = () => report.writes.filter(write => write.method === 'POST' && /^\/api\/agent\/sessions\/[^/]+\/turns$/.test(write.path)).length;
  const postCountBefore = turnPostCount();
  const reopenedResponse = page.waitForResponse(response => new URL(response.url()).pathname === `/api/agent/sessions/${expectedSession}` && response.request().method() === 'GET', { timeout: 30_000 })
    .then(value => ({ value }), error => ({ error }));
  await page.reload({ waitUntil: 'domcontentloaded' });
  const root = await panel();
  const received = await reopenedResponse;
  if (received.error) throw received.error;
  const reopened = await readJson(received.value, 'Final refreshed conversation');
  check(reopened.id === expectedSession && reopened.creator_id === scenario.creator_id, 'Final refresh changed the account conversation');
  const preserved = entry => reopened.entries?.some(value => value.kind === entry.kind && value.turn_id === entry.turn_id && value.text === entry.text && (!entry.id || value.id === entry.id));
  check(preserved(user), 'Final refresh lost the last user query or its server turn ID');
  check(preserved(assistant), 'Final refresh lost the last assistant reply or its server turn ID');
  await root.locator('.chat-user').filter({ hasText: user.text }).last().waitFor({ state: 'visible', timeout: 30_000 });
  const reply = root.locator('.chat-reply').last();
  await reply.locator('.chat-answer').waitFor({ state: 'visible', timeout: 30_000 });
  const tracePromise = page.waitForResponse(response => new URL(response.url()).pathname === `/api/agent/sessions/${expectedSession}/turn-trace/${assistant.turn_id}` && response.request().method() === 'GET', { timeout: 30_000 });
  const [traceResponse] = await Promise.all([tracePromise, reply.getByRole('button', { name: '查看回复 Trace', exact: true }).click()]);
  const trace = await readJson(traceResponse, 'Refreshed visible reply Trace');
  check(trace.turn_id === assistant.turn_id, 'Refreshed visible reply has another server turn ID');
  await page.getByRole('dialog', { name: '回复 Trace', exact: true }).getByRole('button', { name: '关闭 Trace', exact: true }).click();
  const renderedReply = await reply.locator('.chat-answer').innerText();
  check(renderedReply.trim().length > 0, 'Refreshed final reply is empty in the UI');
  check(turnPostCount() === postCountBefore, 'Refreshing the account panel issued an extra turn POST');
  report.refresh = { session_id: expectedSession, creator_id: reopened.creator_id, user: { id: user.id, turn_id: user.turn_id, text: user.text }, assistant: { id: assistant.id, turn_id: assistant.turn_id, text: assistant.text }, rendered_reply: renderedReply, visible_reply_trace: trace, turn_posts_before: postCountBefore, turn_posts_after: turnPostCount() };
  await screenshot('final-refreshed.png');
  check(turnPostCount() === postCountBefore, 'Final refreshed screenshot observed an extra turn POST');
  report.refresh.turn_posts_after = turnPostCount();
  report.refresh_ok = true;
  save();
}

async function completedProductionViews() {
  const deadline = Date.now() + timeoutMs;
  let quiet = 0, state;
  while (Date.now() < deadline) {
    state = await readJson(await context.request.get(`${base}/__benchmark__/task-state?session_id=${report.session_id}`), 'Observe background');
    quiet = state.busy ? 0 : quiet + 1;
    if (quiet >= 2) break;
    await page.waitForTimeout(1000);
  }
  check(quiet >= 2, 'Background tasks did not reach a stable terminal state');
  report.background_terminal = state;
  report.production_pages = [];
  for (const run of state.runs.filter(item => item.status === 'awaiting_approval')) {
    await page.goto(`${base}/runs/${run.id}`, { waitUntil: 'domcontentloaded' });
    await page.locator('.inspector-visual').waitFor({ state: 'visible', timeout: 30_000 });
    const video = page.locator('.run-video-artifact video');
    const observed = { run_id: run.id, url: page.url(), images: [], video: null };
    if (await video.count()) {
      await expect.poll(() => video.evaluate(el => el.readyState >= 1 && el.videoWidth > 0), { timeout: 30_000 }).toBe(true);
      observed.video = await video.evaluate(el => ({ width: el.videoWidth, height: el.videoHeight, duration: el.duration, src: el.currentSrc }));
    } else {
      const choices = page.locator('.carousel-thumbnails button');
      const count = Math.max(1, await choices.count());
      for (let n = 0; n < count; n++) {
        if (await choices.count()) await choices.nth(n).click();
        const picture = page.locator('.carousel-stage img');
        await expect.poll(() => picture.evaluate(el => el.complete && el.naturalWidth > 0), { timeout: 30_000 }).toBe(true);
        observed.images.push(await picture.evaluate(el => ({ width: el.naturalWidth, height: el.naturalHeight, src: el.currentSrc })));
      }
    }
    report.production_pages.push(observed);
    await screenshot(`produced-${run.id}.png`);
    save();
  }
  await page.goto(`${base}/?creator=${encodeURIComponent(scenario.creator_id)}`, { waitUntil: 'domcontentloaded' });
  await panel();
}

try {
  browser = await chromium.launch({ channel: 'chrome', headless: true });
  context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  page = await context.newPage();
  page.on('pageerror', error => { report.errors.push(errorRecord('pageerror', error)); save(); });
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.origin === base && request.method() !== 'GET') report.writes.push({ method: request.method(), path: url.pathname, at: Date.now() });
  });
  scenario = await readJson(await context.request.get(`${base}/__benchmark__/scenario`), 'Read scenario');
  report.case_id = scenario.case_id;
  report.scenario = scenario.scenario;
  check(typeof scenario.creator_id === 'string' && Array.isArray(scenario.steps), 'Invalid benchmark scenario');
  variables = { ...(scenario.variables ?? {}) };
  timeoutMs = Math.max(1, Number(scenario.timeout_seconds ?? 300)) * 1000;
  check(Number.isFinite(timeoutMs), 'Invalid scenario timeout');
  await page.goto(`${base}/?creator=${encodeURIComponent(scenario.creator_id)}`, { waitUntil: 'domcontentloaded' });
  await panel();
  for (const [index, step] of scenario.steps.entries()) {
    if (step.kind === 'user') await userTurn(step, index);
    else if (step.kind === 'event') await hostEvent(step, index);
    else throw new Error(`Unsupported scenario step: ${step.kind}`);
  }
  check(report.turn_posts.length > 0, 'No user turn was submitted through the UI');
  if (scenario.live_codex) await completedProductionViews();
  await finalRefresh();
  check(report.errors.length === 0, 'Browser pageerror occurred; see retained evidence');
  report.completed = true;
  await screenshot('final.png');
} catch (error) {
  report.errors.push(errorRecord('driver_failure', error));
  process.exitCode = 1;
  if (page) try { await screenshot('failure.png'); } catch (captureError) { report.errors.push(errorRecord('failure_screenshot', captureError)); }
} finally {
  if (report.errors.length) report.completed = false;
  save();
  if (context) {
    try {
      firstFinishAttempted = true;
      const response = await context.request.post(`${base}/__benchmark__/finish`, { data: { session_id: report.session_id, browser: report }, timeout: 30_000 });
      report.finish = { status: response.status(), receipt: await readJson(response, 'Finish benchmark') };
    } catch (error) {
      report.errors.push(errorRecord('finish_transport', error));
      report.completed = false;
      process.exitCode = 1;
      // Do not repeat an outcome-unknown POST; only read the retained result.
    }
    const deadline = Date.now() + timeoutMs;
    let settled = false;
    while (Date.now() < deadline) {
      try {
        const response = await context.request.get(`${base}/__benchmark__/result`, { timeout: 15_000 });
        if (response.status() !== 202) {
          report.result = await readJson(response, 'Read benchmark result');
          report.result_status = response.status();
          settled = true;
          break;
        }
      } catch (error) { report.result_poll_last_error = String(error); }
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    if (!settled) { report.errors.push(errorRecord('result_timeout', new Error('Backend did not reach a retained final result'))); report.completed = false; process.exitCode = 1; }
  } else if (!firstFinishAttempted) {
    try {
      const response = await fetch(`${base}/__benchmark__/finish`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: report.session_id, browser: { ...report, completed: false } }),
        signal: AbortSignal.timeout(30_000),
      });
      report.finish = { status: response.status, receipt: await response.json() };
      if (!response.ok) throw new Error(`Failure retention returned HTTP ${response.status}`);
    } catch (error) { report.errors.push(errorRecord('failure_retention_transport', error)); }
  }
  report.finished_at = new Date().toISOString();
  save();
  await browser?.close();
}
