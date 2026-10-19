// Opt-in: real DeepSeek through the actual account chat UI, isolated SQLite/files.
// Start tests.live_authored_skill_server with a fresh ignored --root first.
// node web/e2e/live-direct-pair.mjs http://127.0.0.1:8898 <ignored evidence directory>
import { chromium } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';

const [base, output] = process.argv.slice(2);
if (!base || !output || !/^http:\/\/127\.0\.0\.1:\d+$/.test(base)) throw new Error('Pass isolated loopback URL and evidence directory');
mkdirSync(output, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const report = { base, steps: [], errors: [] };
const check = (condition, message) => { if (!condition) throw new Error(message); };
page.on('pageerror', error => report.errors.push(error.message));
async function api(method, route, data) {
  const response = await page.request.fetch(base + route, { method, data });
  check(response.ok(), `${method} ${route}: ${response.status()} ${await response.text()}`);
  return response.json();
}
try {
  // This evidence route exists ONLY in the isolated, fail-closed live fixture.
  const before = await api('GET', '/api/author-probe/evidence');
  check(before.runs_count === 0 && before.series.length === 0, 'Use a fresh empty fixture');
  const records = {};
  for (const [role, name, description] of [
    ['mind', 'direct-story-mind', '小成就、具体阻碍、行动回扣的故事方法'],
    ['production', 'direct-comic-visual', '暖纸两格漫画呈现方法'],
  ]) {
    const job = await api('POST', '/api/skill-extractions/author', {
      request_id: crypto.randomUUID().replaceAll('-', ''), name, description, role,
      content: `# ${name}\n\n${description}。`, upload_ids: [],
    });
    check(job.status === 'ready', 'Authored fixture did not become ready');
    const saved = await api('POST', `/api/skill-extractions/${job.id}/save`, { expected_digest: job.digest });
    check(saved.status === 'saved' && saved.saved_skills.length === 1, 'Fixture did not save');
    records[role] = saved.saved_skills[0];
  }
  const frozenFiles = (await api('GET', '/api/author-probe/evidence')).files;
  await page.goto(base + '/?creator=author-live-a');
  await page.getByRole('button', { name: '打开账号对话' }).click();
  const panel = page.locator('.account-chat-panel');
  const query = '请在当前账号创建栏目“原始双Skill直用验证”，直接引用 Skill 库的 direct-story-mind 作为内容方法、direct-comic-visual 作为呈现参考。不要融合、不要改写 Skill，不需要定位或受众。只建栏目，不调研、不加选题、不生产、不发布。';
  await panel.getByRole('textbox', { name: '给 Agent 的消息' }).fill(query);
  const submitted = page.waitForResponse(response => response.request().method() === 'POST'
    && /\/api\/agent\/sessions\/[^/]+\/turns$/.test(new URL(response.url()).pathname));
  await panel.getByRole('button', { name: '发送 ↑' }).click();
  const accepted = await submitted;
  check(accepted.status() === 202, `Chat rejected: ${accepted.status()}`);
  const { id } = await accepted.json();
  let view;
  for (let n = 0; n < 360; n++) {
    view = await api('GET', `/api/agent/sessions/${id}`);
    if (view.status !== 'running') break;
    await page.waitForTimeout(500);
  }
  report.query = query;
  report.view = view;
  check(view.status === 'idle', `Chat did not finish: ${view.status}`);
  const tools = view.entries.filter(entry => entry.kind === 'tool');
  report.tools = tools.map(entry => ({ name: entry.name, status: entry.status }));
  check(tools.filter(entry => entry.name === 'compose_series').length === 1, 'Expected one compose_series');
  check(tools.every(entry => ['list_producer_skills', 'list_creator_series', 'compose_series'].includes(entry.name)
    && entry.status === 'done'), `Unexpected/failed tools: ${JSON.stringify(report.tools)}`);
  const all = await api('GET', '/api/series');
  const created = all.filter(series => series.name === '原始双Skill直用验证');
  check(created.length === 1, 'Expected exactly one created series');
  const series = created[0];
  check(series.creator_id === 'author-live-a' && series.skill_name === null
    && series.mind_skill_id === records.mind.id && series.production_skill_id === records.production.id,
  'Series did not retain the original pair IDs');
  await page.screenshot({ path: path.join(output, 'chat-result.png'), fullPage: true });
  await page.reload();
  await panel.waitFor();
  const after = await api('GET', '/api/author-probe/evidence');
  check(after.runs_count === 0 && after.forbidden_calls.length === 0, 'Unexpected paid/production boundary');
  check(JSON.stringify(after.files) === JSON.stringify(frozenFiles), 'Original Skill files changed');
  check(report.errors.length === 0, 'Browser error');
  report.series = series;
  report.evidence = after;
  report.result = 'passed';
  console.log(JSON.stringify({ result: report.result, tools: report.tools, series_id: series.id }));
} catch (error) {
  report.failure = String(error.stack ?? error);
  console.error(report.failure);
  await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }).catch(() => {});
  process.exitCode = 1;
} finally {
  writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
