import { mkdirSync, readFileSync, realpathSync, writeFileSync, existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repo = realpathSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..'));
const within = (root, target) => {
  const relative = path.relative(root, target);
  return !!relative && relative !== '..' && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
};
export function inspectPlanFixture(scenario, args = process.argv.slice(2)) {
  const [requestedRoot, requestedOutput, flag] = args;
  if (!requestedRoot || !requestedOutput || !path.isAbsolute(requestedRoot) || !path.isAbsolute(requestedOutput)
      || !['--live', '--check'].includes(flag)) throw new Error('Pass absolute fresh fixture root, new evidence directory, and --live or --check');
  const boundary = realpathSync(path.join(repo, 'tmp'));
  if (boundary !== path.join(repo, 'tmp')) throw new Error('Checkout tmp must not redirect elsewhere');
  const root = realpathSync(requestedRoot);
  const output = path.join(realpathSync(path.dirname(requestedOutput)), path.basename(requestedOutput));
  if (!within(boundary, root) || !within(boundary, output) || existsSync(output)
      || output === root || within(output, root) || within(root, output)) throw new Error('Use a dedicated fixture root and separate fresh output under checkout tmp');
  const setup = JSON.parse(readFileSync(path.join(root, 'plan-fixture.json'), 'utf8'));
  const url = new URL(setup.url);
  if (setup.root !== root || setup.kind !== 'plan-continuation-v1' || setup.scenario !== scenario || !setup.fixture_id
      || url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port || url.port === '8765'
      || url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw new Error('Fixture identity/scenario/non-production URL does not match');
  const dist = realpathSync(setup.dist);
  const temp = realpathSync(setup.temp);
  if (!within(boundary, dist) || dist === root || within(root, dist) || within(dist, root)
      || output === dist || within(output, dist) || within(dist, output) || !existsSync(path.join(dist, 'index.html'))
      || !within(root, temp)) throw new Error('Build, temp and evidence must be isolated');
  if (flag === '--live' && setup.mode !== 'live') throw new Error('Real DeepSeek requires a --live-models server and --live driver');
  if (existsSync(path.join(root, 'reservation.json')) || existsSync(path.join(root, 'initial-turn.json'))
      || existsSync(path.join(root, 'plan-submission.json'))) throw new Error('Fixture already used: inspect original session/plan; never replay');
  return { setup, root, output, flag };
}

export async function runPlanContinuation(scenario) {
  const { setup, root, output, flag } = inspectPlanFixture(scenario);
  if (flag === '--check') {
    console.log(JSON.stringify({ validated: true, no_browser_or_model: true, fixture_id: setup.fixture_id, mode: setup.mode }));
    return;
  }
  mkdirSync(output);
  Object.assign(process.env, { TEMP: setup.temp, TMP: setup.temp, TMPDIR: setup.temp });
  const { chromium } = await import('@playwright/test');
  const report = { fixture_id: setup.fixture_id, scenario, mode: setup.mode, root, session_id: setup.session_id,
    initial_turn: null, plan: null, passed: false, error: null };
  const save = () => writeFileSync(path.join(output, 'browser-report.json'), JSON.stringify(report, null, 2));
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
    const fixture = await json(await request.get(setup.url + '/test/plan/fixture'));
    if (fixture.fixture_id !== setup.fixture_id || fixture.root !== root || fixture.mode !== 'live'
        || fixture.reserved || fixture.initial_turn_claimed || fixture.plan_claimed) throw new Error('Wrong or already used server instance');
    const reservation = await json(await request.post(setup.url + '/test/plan/reserve', { data: {} }));
    await context.setExtraHTTPHeaders({ 'x-creatoros-plan-token': reservation.token });
    page = await context.newPage();
    await page.goto(`${setup.url}/agent?creator=${setup.creator_id}&chat=${setup.session_id}`);
    const text = scenario === 'review'
      ? '请用后台计划处理隔离测试：先设置request_review检查点，再安排一个依赖它的查询当前栏目选题步骤。检查点触发后，请你判断，把尚未执行的查询选题步骤改为查询当前账号BGM材料，继续计划并汇报。无需问我。只读查询，不创建选题、不改Skill、不生产、不发布。'
      : '接下来测试会注入一个确定发生只读失败的计划。如果读取Skill失败且确认无副作用，请安全skip失败项，把后续步骤从查询选题改成查询账号BGM材料，去除对失败步骤的依赖，然后继续并汇报。现在只确认理解，不要自行创建计划。禁止生产、试产、改Skill、改选题或发布。';
    await page.getByRole('textbox', { name: '给 Agent 的消息' }).fill(text);
    const accepted = page.waitForResponse(response => response.request().method() === 'POST' && /\/turns$/.test(new URL(response.url()).pathname));
    await page.getByRole('button', { name: /发送/ }).click();
    report.initial_turn = await json(await accepted);
    save();
    const sessionPath = `${setup.url}/api/agent/sessions/${setup.session_id}`;
    const planPath = sessionPath + '/plans';
    const waitSession = async () => {
      for (let n = 0; n < 180; n++) {
        const doc = await json(await request.get(sessionPath));
        if (doc.status !== 'running') return doc;
        await page.waitForTimeout(1000);
      }
      throw new Error('Initial DeepSeek turn remains unknown; inspect original session');
    };
    const initial = await waitSession();
    if (initial.status !== 'idle' || initial.error) throw new Error('Initial turn did not complete successfully');
    if (scenario === 'recovery') {
      const first = { id: 'missing', title: '确定发生的只读失败', tool: 'get_producer_skill', arguments: { skill_id: 'missing-skill--0123456789abcdef' } };
      const second = { id: 'next', title: '原定查询选题', tool: 'manage_topics', arguments: { action: 'list', series_id: setup.series_id }, depends_on: ['missing'] };
      report.injected_plan = await json(await request.post(planPath, { headers: { 'x-creatoros-plan-token': reservation.token }, data: { request_id: 'fixture-recovery-' + setup.fixture_id,
        title: '只读失败后的调整与续接', steps: [first, second] } }));
      save();
    }
    let settled = false;
    for (let n = 0; n < 360; n++) {
      const doc = await json(await request.get(sessionPath));
      const listing = await json(await request.get(planPath));
      if (listing.items.length > 1) throw new Error('Expected one plan, found multiple');
      if (listing.items.length === 1) {
        const plan = await json(await request.get(planPath + '/' + listing.items[0].id));
        report.session = doc; report.plan = plan; save();
        if (plan.status === 'completed' && plan.continuation_state === 'done' && doc.status !== 'running') { settled = true; break; }
      }
      await page.waitForTimeout(1000);
    }
    if (!settled) throw new Error('Plan/continuation did not complete; original evidence retained, no replay');
    const { plan, session } = report;
    if (!plan.steps.some(step => step.tool === 'manage_material' && step.status === 'succeeded')) throw new Error('Adjusted BGM lookup did not execute');
    if (scenario === 'review' && !plan.steps.some(step => step.tool === 'request_review' && step.status === 'succeeded')) throw new Error('Checkpoint was avoided');
    if (scenario === 'recovery' && !plan.steps.some(step => step.id === 'missing' && step.status === 'skipped' && step.error)) throw new Error('Known failure/skip evidence was lost');
    if (session.error || session.status !== 'idle') throw new Error('Final continuation did not finish cleanly');
    await page.reload();
    const after = await json(await request.get(sessionPath));
    const preservation = await json(await request.get(setup.url + '/test/plan/fixture'));
    const submitted = JSON.parse(readFileSync(path.join(root, 'plan-submission.json'), 'utf8'));
    report.initial_plan = submitted;
    if (!submitted.payload.steps.some(before => before.tool === 'manage_topics'
        && plan.steps.some(after => after.id === before.id && after.tool === 'manage_material' && after.status === 'succeeded')))
      throw new Error('No original pending step was changed from topic lookup to BGM lookup');
    if (preservation.forbidden_calls.length || (await json(await request.get(planPath))).items.length !== 1
        || after.entries.filter(item => item.kind === 'user').length !== 1
        || preservation.continuations.length < 2
        || new Set(preservation.continuations.map(item => item.event_id)).size !== preservation.continuations.length
        || preservation.continuations.some(item => item.status !== 'idle')) throw new Error('Refresh/continuation duplicated or skipped an event or user turn');
    report.preservation = preservation;
    await page.screenshot({ path: path.join(output, 'final.png'), fullPage: true });
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
}
