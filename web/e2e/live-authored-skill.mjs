// Explicit opt-in: real Chrome, isolated CreatorOS HTTP/SQLite and real DeepSeek.
// node web/e2e/live-authored-skill.mjs http://127.0.0.1:8896 <png> <evidence-dir>
import { chromium } from '@playwright/test';
import { createHash } from 'node:crypto';
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const [base, sourceImage, output] = process.argv.slice(2);
if (!base || !sourceImage || !output) throw new Error('Pass isolated URL, real PNG and evidence directory');
mkdirSync(output, { recursive: true });
const review = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../.impeccable/review');
mkdirSync(review, { recursive: true });
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const sourceHash = hash(readFileSync(sourceImage));
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const report = { base, sourceImage, sourceHash, steps: [], network: [], pageErrors: [], turns: [] };
page.on('pageerror', error => report.pageErrors.push(error.message));
page.on('response', response => {
  const url = new URL(response.url());
  if (url.origin === new URL(base).origin && response.request().method() !== 'GET') {
    report.network.push({ method: response.request().method(), path: url.pathname, status: response.status() });
  }
});
const check = (condition, message) => { if (!condition) throw new Error(message); };
const get = async url => {
  const response = await page.request.get(base + url);
  check(response.ok(), `${url}: ${response.status()} ${await response.text()}`);
  return response.json();
};
const evidence = () => get('/api/author-probe/evidence');
const capture = async (name, reviewName) => {
  const file = path.join(output, name);
  await page.screenshot({ path: file, fullPage: true });
  if (reviewName) copyFileSync(file, path.join(review, reviewName));
  const size = await page.evaluate(() => ({ width: innerWidth, doc: document.documentElement.scrollWidth }));
  check(size.doc <= size.width, `${name}: horizontal overflow ${JSON.stringify(size)}`);
  report.steps.push({ screenshot: file, viewport: size });
};
const jobId = () => new URL(page.url()).searchParams.get('extraction');
const waitJob = async () => {
  await page.getByLabel('提炼任务详情').getByText('任务状态：').waitFor({ timeout: 15000 });
  const id = jobId();
  check(id, `Missing extraction id in ${page.url()}`);
  const job = await get(`/api/skill-extractions/${id}`);
  check(job.status === 'ready', `Job ${id} not ready: ${job.status}`);
  return job;
};
const fill = async (form, { name, display, description, content }) => {
  await form.getByLabel('显示名称（可选）').fill(display);
  await form.getByLabel('英文标识').fill(name);
  await form.getByLabel('用途说明').fill(description);
  await form.getByLabel('提示词或完整 SKILL.md').fill(content);
};
const mark = step => { report.steps.push(step); console.log(JSON.stringify(step)); };
async function chat(panel, message, expectedCalls = []) {
  const previousEntries = report.turns.at(-1)?.entries.length ?? 0;
  const input = panel.getByRole('textbox', { name: '给 Agent 的消息' });
  await input.fill(message);
  const pending = page.waitForResponse(response => response.request().method() === 'POST' &&
    /\/api\/agent\/sessions\/[^/]+\/turns$/.test(new URL(response.url()).pathname), { timeout: 15000 });
  await panel.getByRole('button', { name: '发送 ↑' }).click();
  const acceptedResponse = await pending;
  check(acceptedResponse.status() === 202, `Chat submit: ${acceptedResponse.status()} ${await acceptedResponse.text()}`);
  const accepted = await acceptedResponse.json();
  let view;
  for (let tries = 0; tries < 360; tries++) {
    view = await get(`/api/agent/sessions/${accepted.id}`);
    if (view.status !== 'running') break;
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  check(view.status === 'idle', `Chat incomplete: ${JSON.stringify(view)}`);
  const newEntries = view.entries.slice(previousEntries);
  const tools = newEntries.filter(entry => entry.kind === 'tool');
  const calls = tools.map(entry => entry.name);
  report.turns.push({ sessionId: accepted.id, message, status: view.status, entries: view.entries, calls,
    usage: newEntries.filter(entry => entry.kind === 'usage'), error: view.error });
  check(JSON.stringify(calls) === JSON.stringify(expectedCalls),
    `Unexpected new tools for this turn: expected ${expectedCalls}, actual ${calls}`);
  check(tools.every(entry => entry.status === 'done'), `Tool did not finish: ${JSON.stringify(tools)}`);
  const forbidden = ['extract_skill_from_images', 'extract_skills_from_artifact', 'revise_extracted_skills',
    'trial_extracted_skills', 'start_content_run', 'research_series_topics', 'route_and_answer'];
  check(!calls.some(name => forbidden.includes(name)), `Forbidden/unexpected model tool: ${calls}`);
  mark({ chat: message, status: view.status, calls, sessionId: accepted.id });
  return view;
}
try {
  await page.goto(base + '/skills');
  await page.getByRole('heading', { name: 'Skill 库' }).waitFor();
  await page.getByRole('button', { name: '自己写 Skill' }).click();
  const form = page.getByRole('form', { name: '自己写 Skill' });
  await fill(form, { name: 'plain-live', display: '无图原稿', description: '保存已经调好的纯文字提示词',
    content: '原样保留：三段式解释，最后给出一条可核对的例子。' });
  await capture('author-desktop.png', 'author-desktop.png');
  await page.setViewportSize({ width: 390, height: 844 });
  await capture('author-mobile.png', 'author-mobile.png');
  await page.setViewportSize({ width: 1440, height: 900 });
  const noImagePost = page.waitForResponse(response => new URL(response.url()).pathname === '/api/skill-extractions/author');
  await form.getByRole('button', { name: '保存为草稿' }).click();
  check((await noImagePost).status() === 201, 'No-image author POST failed');
  const plain = await waitJob();
  check(plain.uploads.length === 0 && plain.task_kind === 'author', 'Plain author job shape wrong');
  mark({ plainJob: plain.id, status: plain.status, uploads: plain.uploads.length });

  await page.getByRole('button', { name: '自己写 Skill' }).click();
  await fill(form, { name: 'mismatch-live', display: '错误保留', description: '校验 frontmatter 名称',
    content: '---\nname: another-name\ndescription: 原样校验\n---\n这个输入必须保留。' });
  await form.getByRole('button', { name: '保存为草稿' }).click();
  await form.getByRole('alert').getByText(/name|名称|一致/).waitFor();
  check((await form.getByLabel('提示词或完整 SKILL.md').inputValue()).includes('这个输入必须保留'), 'Mismatch erased input');
  mark({ mismatch: 'rejected, input retained' });
  await form.getByLabel('提示词或完整 SKILL.md').fill('---\nname: picture-live\ndescription: 参考图保存\n---\n按这段原稿制作，保留图片引用。');
  await form.getByLabel('英文标识').fill('picture-live');
  await form.getByLabel('用途说明').fill('参考图保存');
  await form.locator('input[type=file]').setInputFiles(sourceImage);
  await form.getByLabel('已选图片').waitFor();
  const imagePost = page.waitForResponse(response => new URL(response.url()).pathname === '/api/skill-extractions/author');
  await form.getByRole('button', { name: '保存为草稿' }).click();
  const imageResponse = await imagePost;
  check(imageResponse.status() === 201, `Image author POST failed: ${imageResponse.status()} ${await imageResponse.text()}`);
  const pictured = await waitJob();
  check(pictured.uploads.length === 1, 'Image author job missing upload');
  const snapshot = await evidence();
  check(snapshot.files.some(file => file.path.includes(pictured.id) && file.sha256 === sourceHash), 'Draft asset hash mismatch');
  const imageFile = pictured.files.find(file => file.kind === 'image');
  check(imageFile, 'No image in draft file tree');
  await page.getByLabel('Skill 文件').getByRole('button', { name: imageFile.path }).click();
  await page.getByAltText(imageFile.path).waitFor();
  await page.reload();
  const reloaded = await waitJob();
  check(reloaded.id === pictured.id, 'Reload selected wrong draft');
  mark({ imageJob: pictured.id, imageHash: sourceHash, reloaded: true });

  await page.goto(base + '/?creator=author-live-a');
  await page.getByRole('button', { name: '打开账号对话' }).click();
  const panel = page.locator('.account-chat-panel');
  await panel.getByRole('heading', { name: '[隔离验证] 创作实验室' }).waitFor();
  await panel.locator('input[type=file][aria-label="选择图片文件"]').setInputFiles(sourceImage);
  await panel.getByRole('img', { name: path.basename(sourceImage) }).waitFor({ timeout: 15000 });
  const original = '先解释概念；再举一个具体例子；最后列一个可核对的检查点。';
  const prompt = `我已经写好下面这段可复用提示词，请原样保存为名为 account-original 的 Skill 草稿，显示名称“账号原稿”，用途是“保留三段解释写法”。我附了一张参考图，也请一起保存。不要从图中总结规则，不要提炼、试产或生产：\n${original}`;
  const first = await chat(panel, prompt, ['create_skill_draft']);
  const created = first.entries.filter(entry => entry.kind === 'tool' && entry.name === 'create_skill_draft');
  check(created.length === 1, `Expected exactly one create_skill_draft, found ${created.length}`);
  check((await evidence()).runs_count === 0, 'Chat created a production run');
  const jobs = await get('/api/skill-extractions');
  const accountJob = jobs.items.find(job => job.skills?.some(skill => skill.name === 'account-original'));
  check(accountJob && accountJob.status === 'ready', 'Account draft absent from isolated SQLite');
  const beforeSave = await evidence();
  check(beforeSave.files.some(file => file.path.includes(accountJob.id) && file.sha256 === sourceHash), 'Chat draft image hash mismatch');
  const draftSource = beforeSave.files.find(file => file.path.includes(accountJob.id) &&
    file.path.endsWith('/authored/legacy_end_to_end/assets/source.txt'));
  check(draftSource && readFileSync(path.join(beforeSave.root, draftSource.path), 'utf8') === original,
    'Chat draft source.txt differs from user-authored text');
  await page.reload();
  await panel.getByText('先解释概念；再举一个具体例子', { exact: false }).waitFor();
  const second = await chat(panel,
    '我确认刚才的 account-original 草稿可以加入 Skill 库，并用这个已入库的 Skill 在当前“创作实验室”账号下创建栏目“原稿练习”。不要试产、生产或发布。',
    ['save_extracted_skills', 'compose_series']);
  const afterSave = await evidence();
  check(afterSave.series.some(series => series.creator_id === 'author-live-a' && series.name === '原稿练习' &&
    series.skill_name?.includes('account-original')), `No bound series: ${JSON.stringify(afterSave.series)}`);
  check(afterSave.runs_count === 0 && afterSave.forbidden_calls.length === 0, 'Forbidden boundary called');
  check(afterSave.files.some(file => file.path.includes('account-original') && file.sha256 === sourceHash), 'Library image hash mismatch');
  const installedSource = afterSave.files.find(file => file.path ===
    `skills/working/${afterSave.series[0].skill_name}/assets/source.txt`);
  check(installedSource && readFileSync(path.join(afterSave.root, installedSource.path), 'utf8') === original,
    'Library source.txt differs from user-authored text');
  check(!report.pageErrors.length, `Page errors: ${report.pageErrors}`);
  mark({ result: 'passed', plainJob: plain.id, imageJob: pictured.id, accountJob: accountJob.id,
    series: afterSave.series, firstCalls: report.turns[0].calls, secondCalls: report.turns[1].calls });
} catch (error) {
  report.failure = String(error?.stack ?? error);
  console.error(report.failure);
  try { await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }); } catch { /* preserve prior evidence */ }
  process.exitCode = 1;
} finally {
  try { report.finalEvidence = await evidence(); } catch (error) { report.evidenceError = String(error); }
  writeFileSync(path.join(output, 'browser-report.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
