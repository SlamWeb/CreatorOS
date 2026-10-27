// Opt-in paid probe against a separately seeded local server, never production data.
import { chromium } from '@playwright/test';
import { readFileSync, writeFileSync } from 'node:fs';
const [base, setupPath, outputPath, resumeSession] = process.argv.slice(2);
if (!base || !setupPath || !outputPath) throw new Error('Expected base URL, fixture setup and report path');
const fixture = JSON.parse(readFileSync(setupPath, 'utf8'));
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage();
try {
  await page.goto(`${base}/agent?creator=${fixture.creator_id}${resumeSession ? `&chat=${resumeSession}` : ''}`);
  await page.getByText('账号：读取中…').waitFor({ state: 'hidden', timeout: 30000 });
  const oldPlans = resumeSession ? (await (await page.request.get(`${base}/api/agent/sessions/${resumeSession}/plans`)).json()).items.map(p => p.id) : [];
  const text = resumeSession
    ? '继续验证：直接使用上个计划创建的独立素材集“从清晨到黄昏-清晨黄昏两帧”，用 image-beat-story 和已有音乐制作一条约六秒的竖版视频。不要重新生成图片。请提交计划，完成后自动告诉我结果。'
    : '请为栏目“一天的世界”的已有选题“从清晨到黄昏”完成一个视频。先用像素生图 Skill 并行制作两张不同时间的图，再把这两张本次生成的图收集，使用 image-beat-story 和账号已有音频素材制作一个短视频。请制定计划让后台自动依次执行并在完成后继续，不要轮询；失败时如实说明。';
  const input = page.getByRole('textbox', { name: '给 Agent 的消息' });
  await input.fill(text);
  const pending = page.waitForResponse(r => r.url().endsWith('/turns') && r.request().method() === 'POST');
  await page.getByRole('button', { name: /发送/ }).click();
  const accepted = await pending;
  if (!accepted.ok()) throw new Error(await accepted.text());
  const session = await accepted.json();
  console.log(`session=${session.id}`);
  let previous = '';
  let newPlan = null;
  let finished = false;
  for (let n = 0; n < 1800; n++) {
    const view = await (await page.request.get(`${base}/api/agent/sessions/${session.id}`)).json();
    const plans = await (await page.request.get(`${base}/api/agent/sessions/${session.id}/plans`)).json();
    const signature = JSON.stringify({ chat: view.status, plans: plans.items });
    if (signature !== previous) { console.log(signature); previous = signature; }
    if (plans.items?.length) newPlan = plans.items.find(p => !oldPlans.includes(p.id)) || newPlan;
    if (newPlan && ['completed', 'needs_attention'].includes(newPlan.status) && view.status !== 'running') {
      // Give the scheduler a moment to deliver its continuation.
      await page.waitForTimeout(3000);
      const final = await (await page.request.get(`${base}/api/agent/sessions/${session.id}`)).json();
      const detail = await (await page.request.get(`${base}/api/agent/sessions/${session.id}/plans/${newPlan.id}`)).json();
      if (final.status === 'running') { await page.waitForTimeout(2000); continue; }
      writeFileSync(outputPath, JSON.stringify({ session: final, plan: detail }, null, 2));
      console.log(`final=${detail.status} chat=${final.status}`);
      finished = true;
      break;
    }
    await page.waitForTimeout(2000);
  }
  if (!finished) throw new Error('Timed out before a new plan and its continuation settled');
} finally { await browser.close(); }
