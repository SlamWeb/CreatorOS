// Explicit opt-in: real DeepSeek on an isolated server, not part of default E2E.
// node web/e2e/live-agent.mjs http://127.0.0.1:8884 <isolated-output-directory>
import { chromium } from '@playwright/test';
import { writeFileSync } from 'node:fs';
import path from 'node:path';

const [base, output, resume] = process.argv.slice(2);
if (!base || !output) throw new Error('Pass isolated server URL and evidence directory');
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
page.on('pageerror', e => errors.push(e.message));
try {
  await page.goto(base + '/agent' + (resume ? `?chat=${resume}` : ''));
  await page.getByRole('heading', { name: '把想法交给 Agent' }).waitFor();
  await page.screenshot({ path: path.join(output, 'empty.png'), fullPage: true });
  let sid = resume;
  async function chat(text) {
    await page.getByRole('textbox', { name: '给 Agent 的消息' }).fill(text);
    const pending = page.waitForResponse(r => r.url().endsWith('/turns') && r.request().method() === 'POST');
    await page.getByRole('button', { name: '发送 ↑' }).click();
    const response = await pending;
    if (response.status() !== 202) throw new Error(await response.text());
    const accepted = await response.json();
    sid = accepted.id;
    let view;
    let sawPartial = false;
    for (let n = 0; n < 240; n++) {
      view = await (await page.request.get(`${base}/api/agent/sessions/${sid}`)).json();
      if (view.status === 'running' && view.entries.some(e => e.kind === 'assistant' && e.text)) sawPartial = true;
      if (view.status !== 'running') break;
      await new Promise(r => setTimeout(r, 500));
    }
    if (view.status !== 'idle') throw new Error(JSON.stringify(view));
    await page.getByRole('status').filter({ hasText: '可以继续对话' }).waitFor();
    console.log(JSON.stringify({ status: view.status, sawPartial, calls: view.entries.filter(e => e.kind === 'tool').map(e => e.name) }));
    return view;
  }
  if (!resume) await chat('看看我有哪些账号和栏目，先不要生产。');
  await page.reload();
  await page.getByLabel('对话记录').getByText('看看我有哪些账号和栏目，先不要生产。', { exact: true }).waitFor();
  await chat('把知识实验室账号的把 Agent 讲明白栏目里 Agent State、Context 和 Messages 这篇做出来。');
  const final = await chat('刚才那条任务现在是什么状态？帮我查一下。');
  const calls = final.entries.filter(e => e.kind === 'tool').map(e => e.name);
  for (const name of ['list_creators', 'list_creator_series', 'list_series_topics', 'start_content_run', 'get_content_run']) {
    if (!calls.includes(name)) throw new Error(`Missing real tool call: ${name}`);
  }
  const runs = await (await page.request.get(base + '/api/runs')).json();
  if (runs.items.length !== 1 || runs.items[0].status !== 'cancelled') throw new Error('Unexpected production mutation');
  await page.screenshot({ path: path.join(output, 'desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: path.join(output, 'mobile.png'), fullPage: true });
  if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error('Mobile overflow');
  await page.getByRole('link', { name: '查看内容任务 ↗' }).last().click();
  await page.getByRole('heading', { name: 'Agent State、Context 和 Messages' }).waitFor();
  if (errors.length) throw new Error(errors.join('\n'));
  writeFileSync(path.join(output, 'browser-report.json'), JSON.stringify({ session_id: sid, calls,
    usage: final.entries.filter(e => e.kind === 'usage'), status: final.status, errors }, null, 2));
  console.log(`live_web_agent=passed session=${sid} directory=${output}`);
} finally {
  await browser.close();
}
