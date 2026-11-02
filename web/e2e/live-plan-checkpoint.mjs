// Fresh isolated timing proof. --controlled is a local-provider fixture check,
// never real-model evidence. --live uses the real DeepSeek provider.
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { inspectPlanFixture } from './plan-continuation-fixture.mjs';

const [requestedRoot, requestedOutput, mode] = process.argv.slice(2);
if (!['--check', '--controlled', '--live'].includes(mode)) throw new Error('Specify --check, --controlled or --live');
const { setup, root, output } = inspectPlanFixture('checkpoint', [requestedRoot, requestedOutput, '--check']);
if (mode === '--check') {
  console.log(JSON.stringify({ validated: true, no_browser_or_model: true, fixture_id: setup.fixture_id, mode: setup.mode }));
  process.exit(0);
}
if (setup.mode !== (mode === '--live' ? 'live' : 'controlled')) throw new Error('Driver mode must match this fixture');
mkdirSync(output);
Object.assign(process.env, { TEMP: setup.temp, TMP: setup.temp, TMPDIR: setup.temp });
const { chromium } = await import('@playwright/test');
const report = { fixture_id: setup.fixture_id, scenario: 'checkpoint', mode: setup.mode, root,
  session_id: setup.session_id, passed: false, error: null };
const save = () => writeFileSync(path.join(output, 'browser-report.json'), JSON.stringify(report, null, 2));
const ensure = (condition, message) => { if (!condition) throw new Error(message); };
let browser, context, page;
save();
try {
  browser = await chromium.launch({ channel: 'chrome', headless: true });
  context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await context.tracing.start({ screenshots: true, snapshots: true });
  const request = context.request;
  const json = async response => {
    if (!response.ok()) throw new Error(`HTTP ${response.status()}: ${(await response.text()).slice(0, 1000)}`);
    return response.json();
  };
  const initialFixture = await json(await request.get(setup.url + '/test/plan/fixture'));
  ensure(initialFixture.fixture_id === setup.fixture_id && initialFixture.root === root && initialFixture.mode === setup.mode
    && !initialFixture.reserved && !initialFixture.initial_turn_claimed && !initialFixture.plan_claimed
    && !initialFixture.checkpoint_gate_released && initialFixture.continuations.length === 0,
  'Wrong, already used, or prematurely released fixture');
  const reservation = await json(await request.post(setup.url + '/test/plan/reserve', { data: {} }));
  await context.setExtraHTTPHeaders({ 'x-creatoros-plan-token': reservation.token });
  page = await context.newPage();
  await page.goto(`${setup.url}/agent?creator=${setup.creator_id}&chat=${setup.session_id}`);
  const query = '请创建一个后台只读计划，本轮只创建计划然后结束回复。第一步是 request_review 检查点，检查点的问题写明：触发后，请把原定查询栏目选题的步骤改为查询当前账号 BGM 音频材料并继续计划。第二步是查询当前栏目的选题，依赖第一步。检查点触发前不要修改、恢复或执行后续步骤；待系统在后台唤醒你后再根据检查点要求调整。不要创建选题、改 Skill、生产或发布。';
  await page.getByRole('textbox', { name: '给 Agent 的消息' }).fill(query);
  const accepted = page.waitForResponse(response => response.request().method() === 'POST' && /\/turns$/.test(new URL(response.url()).pathname));
  await page.getByRole('button', { name: /发送/ }).click();
  report.initial_turn = await json(await accepted);
  report.query = query;
  save();
  const sessionPath = `${setup.url}/api/agent/sessions/${setup.session_id}`;
  const planPath = sessionPath + '/plans';
  let initialSession;
  for (let n = 0; n < 180; n++) {
    initialSession = await json(await request.get(sessionPath));
    if (initialSession.status !== 'running') break;
    await page.waitForTimeout(1000);
  }
  ensure(initialSession?.status === 'idle' && !initialSession.error, 'Initial user turn did not finish cleanly');
  const beforeFixture = await json(await request.get(setup.url + '/test/plan/fixture'));
  const beforeList = await json(await request.get(planPath));
  ensure(beforeList.items.length === 1 && beforeFixture.continuations.length === 0
    && !beforeFixture.checkpoint_gate_released && beforeFixture.plan_dispatches.length === 0,
  'The initial plan/continuation ran before release');
  const before = await json(await request.get(planPath + '/' + beforeList.items[0].id));
  ensure(before.steps.length === 2 && before.steps[0].tool === 'request_review'
    && before.steps[1].tool === 'manage_topics' && before.steps.every(step => step.status === 'pending')
    && before.steps[1].depends_on.includes(before.steps[0].id), 'Initial plan is not the requested untouched two-step plan');
  report.before_release = { session: initialSession, plan: before, fixture: beforeFixture };
  await page.screenshot({ path: path.join(output, 'before-release.png'), fullPage: true });
  save();
  report.release = await json(await request.post(setup.url + '/test/plan/release', { data: {} }));
  report.gate_receipt = JSON.parse(readFileSync(path.join(root, 'gate-release.json'), 'utf8'));
  ensure(report.release.plan_id === before.id && report.gate_receipt.plan_before.id === before.id,
    'Release did not use the original plan');
  save();
  let finalFixture, finalSession, finalPlan;
  for (let n = 0; n < 360; n++) {
    finalSession = await json(await request.get(sessionPath));
    const listing = await json(await request.get(planPath));
    ensure(listing.items.length === 1 && listing.items[0].id === before.id, 'Plan identity changed or duplicated');
    finalPlan = await json(await request.get(planPath + '/' + before.id));
    finalFixture = await json(await request.get(setup.url + '/test/plan/fixture'));
    report.final = { session: finalSession, plan: finalPlan, fixture: finalFixture };
    save();
    if (finalPlan.status === 'completed' && finalPlan.continuation_state === 'done' && finalSession.status === 'idle') break;
    await page.waitForTimeout(1000);
  }
  ensure(finalPlan.status === 'completed' && finalPlan.continuation_state === 'done' && finalSession.status === 'idle'
    && !finalSession.error, 'The checkpoint plan did not complete cleanly');
  ensure(finalPlan.steps.some(step => step.id === before.steps[0].id && step.tool === 'request_review' && step.status === 'succeeded'),
    'Original checkpoint did not execute');
  ensure(finalPlan.steps.some(step => step.tool === 'manage_material' && step.arguments.action === 'list' && step.status === 'succeeded'),
    'Adjusted BGM lookup did not succeed');
  ensure(finalFixture.plan_dispatches.length === 1 && finalFixture.plan_dispatches[0].tool === 'manage_material'
    && JSON.parse(finalFixture.plan_dispatches[0].arguments).action === 'list'
    && Date.parse(finalFixture.plan_dispatches[0].started_at) >= Date.parse(report.gate_receipt.released_at),
  'Expected one post-checkpoint BGM dispatch and no original topic dispatch');
  ensure(finalFixture.continuations.length >= 2
    && new Set(finalFixture.continuations.map(item => item.event_id)).size === finalFixture.continuations.length
    && finalFixture.continuations.every(item => item.status === 'idle') && finalFixture.forbidden_calls.length === 0,
  'Continuation/forbidden-call evidence is incomplete');
  report.continuation_traces = [];
  let confirmedUpdate = false;
  for (const continuation of finalFixture.continuations) {
    const trace = await json(await request.get(`${sessionPath}/turn-trace/${continuation.id}`));
    const snapshots = [];
    for (const item of trace.requests) {
      ensure(Date.parse(item.started_at) >= Date.parse(report.gate_receipt.released_at), 'Continuation began before checkpoint release');
      const snapshot = await json(await request.get(`${sessionPath}/turn-trace/${continuation.id}/requests/${item.request_id}`));
      snapshots.push(snapshot);
      for (const call of snapshot.response?.tool_calls ?? []) {
        if (call.name !== 'manage_plan') continue;
        const args = JSON.parse(call.arguments);
        const result = snapshot.tool_results?.find(value => value.tool_call_id === call.id);
        if (args.action === 'update' && args.steps?.some(step => step.tool === 'manage_material' && step.arguments?.action === 'list')
            && result && !result.is_error) confirmedUpdate = true;
      }
    }
    report.continuation_traces.push({ continuation, trace, snapshots });
    save();
  }
  ensure(confirmedUpdate, 'No successful plan adjustment by a post-checkpoint continuation was observed');
  await page.reload();
  const after = await json(await request.get(sessionPath));
  const afterFixture = await json(await request.get(setup.url + '/test/plan/fixture'));
  ensure(after.entries.filter(item => item.kind === 'user').length === 1 && afterFixture.plan_dispatches.length === 1
    && afterFixture.continuations.length === finalFixture.continuations.length
    && (await json(await request.get(planPath))).items.length === 1, 'Refresh repeated a task, user turn or continuation');
  report.after_refresh = { session: after, fixture: afterFixture };
  await page.screenshot({ path: path.join(output, 'after-checkpoint.png'), fullPage: true });
  report.passed = true;
} catch (error) {
  report.error = String(error.stack ?? error);
  throw error;
} finally {
  if (!report.passed && page) await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }).catch(() => {});
  if (context) await context.tracing.stop({ path: path.join(output, 'trace.zip') }).catch(error => { report.trace_error = String(error); });
  save();
  await browser?.close();
}
