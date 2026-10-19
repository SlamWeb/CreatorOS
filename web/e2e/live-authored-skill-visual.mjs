// UI-only Chrome review of the authored Skill form and mobile chat launcher.
// node web/e2e/live-authored-skill-visual.mjs http://127.0.0.1:8896 <evidence-dir>
import { chromium } from '@playwright/test';
import { copyFileSync, mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const [base, output] = process.argv.slice(2);
if (!base || !output) throw new Error('Pass isolated URL and evidence directory');
mkdirSync(output, { recursive: true });
const review = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../.impeccable/review');
mkdirSync(review, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const report = { base, screenshots: [], geometry: {}, writes: [], pageErrors: [] };
page.on('pageerror', error => report.pageErrors.push(error.message));
page.on('request', request => {
  if (new URL(request.url()).origin === new URL(base).origin && request.method() !== 'GET')
    report.writes.push({ method: request.method(), path: new URL(request.url()).pathname });
});
const check = (condition, message) => { if (!condition) throw new Error(message); };
const box = async selector => {
  const rect = await selector.boundingBox();
  check(rect, `Missing geometry for ${selector}`);
  return rect;
};
const intersects = (a, b) => a.x < b.x + b.width && a.x + a.width > b.x &&
  a.y < b.y + b.height && a.y + a.height > b.y;
const capture = async (name) => {
  const file = path.join(output, name);
  await page.screenshot({ path: file, fullPage: true });
  copyFileSync(file, path.join(review, name));
  const viewport = await page.evaluate(() => ({ width: innerWidth, documentWidth: document.documentElement.scrollWidth }));
  check(viewport.documentWidth <= viewport.width, `${name}: horizontal overflow`);
  report.screenshots.push({ file, viewport });
};
try {
  await page.goto(base + '/skills');
  await page.getByRole('heading', { name: 'Skill 库' }).waitFor();
  await page.getByRole('button', { name: '自己写 Skill' }).click();
  const form = page.getByRole('form', { name: '自己写 Skill' });
  await form.getByLabel('显示名称（可选）').fill('无图原稿');
  await form.getByLabel('英文标识').fill('plain-live');
  await form.getByLabel('用途说明').fill('保存已经调好的纯文字提示词');
  await form.getByLabel('提示词或完整 SKILL.md').fill('原样保留：三段式解释，最后给出一条可核对的例子。');
  const launcher = page.locator('.workspace-chat-launcher');
  await capture('author-desktop.png');
  report.geometry.desktop = { launcher: await box(launcher), textarea: await box(form.getByRole('textbox', { name: '提示词或完整 SKILL.md' })) };
  await page.setViewportSize({ width: 390, height: 844 });
  await capture('author-mobile.png');
  const mobile = {
    launcher: await box(launcher),
    textarea: await box(form.getByRole('textbox', { name: '提示词或完整 SKILL.md' })),
    save: await box(form.getByRole('button', { name: '保存为草稿' })),
  };
  report.geometry.mobileOpen = mobile;
  check(mobile.launcher.x < mobile.textarea.x, 'Launcher did not move into the left navigation gutter');
  check(!intersects(mobile.launcher, mobile.textarea), 'Launcher overlaps text editor');
  check(!intersects(mobile.launcher, mobile.save), 'Launcher overlaps Save button');
  await page.getByRole('button', { name: '打开账号对话' }).click();
  await page.locator('.account-chat-panel').waitFor();
  report.geometry.chatOpened = true;
  await page.locator('.account-chat-panel').getByRole('button', { name: '关闭账号对话' }).click();
  check(await launcher.getAttribute('aria-expanded') === 'false', 'Chat did not close');
  await page.getByRole('button', { name: '收起编写' }).click();
  const closed = { launcher: await box(launcher), viewportWidth: 390 };
  report.geometry.mobileClosed = closed;
  check(closed.launcher.x > 300, `Launcher did not return to the right: ${JSON.stringify(closed)}`);
  check(!report.writes.length, `UI-only test issued writes: ${JSON.stringify(report.writes)}`);
  check(!report.pageErrors.length, `Browser errors: ${report.pageErrors}`);
  report.result = 'passed';
  console.log(JSON.stringify(report));
} catch (error) {
  report.result = 'failed';
  report.failure = String(error?.stack ?? error);
  try { await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }); } catch { /* no page */ }
  console.error(report.failure);
  process.exitCode = 1;
} finally {
  writeFileSync(path.join(output, 'visual-report.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
