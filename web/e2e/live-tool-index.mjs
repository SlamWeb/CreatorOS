// Explicit paid opt-in; real SPA -> DeepSeek -> HTTP -> isolated SQLite.
// node web/e2e/live-tool-index.mjs <isolated-base> <root-containing-setup.json>
import { chromium } from '@playwright/test';
import { copyFileSync, existsSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';

const [base, root] = process.argv.slice(2);
if (!base || !root) throw new Error('Pass isolated base URL and probe root');
const setup = JSON.parse(readFileSync(path.join(root, 'setup.json'), 'utf8'));
const reportPath = path.join(root, 'browser-report.json');
if (existsSync(reportPath)) copyFileSync(reportPath, path.join(root, `browser-report-${Date.now()}.json`));
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const report = { setup, turns: [], errors: [] };
const check = (ok, message) => { if (!ok) throw new Error(message); };
page.on('pageerror', error => report.errors.push(error.message));
try {
  await page.goto(`${base}/?creator=${setup.creator_id}`);
  await page.getByRole('button', { name: '打开账号对话' }).click();
  const panel = page.locator('.account-chat-panel');
  let previous = 0;
  for (const text of setup.queries) {
    await panel.getByRole('textbox', { name: '给 Agent 的消息' }).fill(text);
    const pending = page.waitForResponse(r => r.request().method() === 'POST' && /\/sessions\/[^/]+\/turns$/.test(new URL(r.url()).pathname));
    await panel.getByRole('button', { name: '发送 ↑' }).click();
    const response = await pending;
    check(response.status() === 202, `Turn submit ${response.status()}: ${await response.text()}`);
    const accepted = await response.json();
    let view;
    for (let n = 0; n < 240; n++) {
      view = await (await page.request.get(`${base}/api/agent/sessions/${accepted.id}`)).json();
      if (view.status !== 'running') break;
      await page.waitForTimeout(1000);
    }
    check(view.status === 'idle', `Turn incomplete: ${view.error ?? view.status}`);
    const entries = view.entries.slice(previous);
    previous = view.entries.length;
    const turn = { text, session_id: accepted.id, entries,
      calls: entries.filter(e => e.kind === 'tool').map(e => ({ name: e.name, arguments: e.arguments, status: e.status })) };
    report.turns.push(turn);
    console.log(JSON.stringify({ text, calls: turn.calls }));
    check(turn.calls.every(c => c.status === 'done'), 'Tool failed');
    check(!turn.calls.some(c => ['start_content_run', 'research_series_topics', 'manage_plan'].includes(c.name)), 'Unexpected background task');
    writeFileSync(path.join(root, 'browser-report.json'), JSON.stringify(report, null, 2));
  }
  check(report.turns[0].calls.some(c => c.name === 'manage_topics'), 'Initial list was not read');
  check(!report.turns[1].calls.some(c => c.name === 'manage_topics'), 'Unchanged complete list was queried again');
  check(report.turns[2].calls.filter(c => c.name === 'manage_topics').length === 1, 'Detail was not fetched directly');
  const answer = report.turns[2].entries.filter(e => e.kind === 'assistant').map(e => e.text).join('\n');
  check(answer.includes('小蓝点'), 'Full detail tail was lost');
  const topics = await (await page.request.get(`${base}/api/series/index-series/topics?limit=100`)).json();
  check(topics.items.length === setup.topic_count, 'Topics were added or deleted');
  check(report.errors.length === 0, report.errors.join('\n'));
  await page.screenshot({ path: path.join(root, 'chat.png'), fullPage: true });
  report.passed = true;
} catch (error) {
  report.failure = error.message;
  throw error;
} finally {
  writeFileSync(path.join(root, 'browser-report.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
