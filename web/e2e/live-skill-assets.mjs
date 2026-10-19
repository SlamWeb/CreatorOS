// Explicit opt-in. Real Chrome against the isolated live_skill_assets_server fixture.
// node web/e2e/live-skill-assets.mjs <base-url> <original-png> <replacement-png> <evidence-dir> [--baseline|--visual]
import { chromium } from '@playwright/test';
import { createHash } from 'node:crypto';
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const [base, originalPng, replacementPng, output, mode] = process.argv.slice(2);
if (!base || !originalPng || !replacementPng || !output) {
  throw new Error('Pass isolated URL, two distinct real PNGs, and ignored evidence directory');
}
mkdirSync(output, { recursive: true });
const review = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../.impeccable/review/skill-assets');
mkdirSync(review, { recursive: true });
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const inputHashes = [hash(readFileSync(originalPng)), hash(readFileSync(replacementPng))];
if (inputHashes[0] === inputHashes[1]) throw new Error('The two PNG files must differ');
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const report = { mode: mode ?? 'full', base, inputHashes, steps: [], network: [], pageErrors: [], turns: [], requests: [] };
const check = (condition, message) => { if (!condition) throw new Error(message); };
page.on('pageerror', error => report.pageErrors.push(error.message));
page.on('response', response => {
  const url = new URL(response.url());
  if (url.origin === new URL(base).origin && response.request().method() !== 'GET') {
    report.network.push({ method: response.request().method(), path: url.pathname, status: response.status() });
  }
});
const get = async endpoint => {
  const response = await page.request.get(base + endpoint);
  check(response.ok(), `${endpoint}: ${response.status()} ${await response.text()}`);
  return response.json();
};
const evidence = () => get('/api/skill-assets-probe/evidence');
const mark = value => { report.steps.push(value); console.log(JSON.stringify(value)); };
const capture = async (name, reviewName = name) => {
  const file = path.join(output, name);
  await page.screenshot({ path: file, fullPage: true });
  copyFileSync(file, path.join(review, reviewName));
  const size = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth }));
  check(size.document <= size.viewport, `${name}: horizontal overflow ${JSON.stringify(size)}`);
  mark({ screenshot: file, size });
};
const mainId = async () => (await evidence()).setup.skill_ids['asset-live-main'];
const skillFiles = id => get(`/api/producer-skills/${encodeURIComponent(id)}/files`);
const assetBytes = async (id, filePath) => {
  const response = await page.request.get(`${base}/api/producer-skills/${encodeURIComponent(id)}/files/content?${new URLSearchParams({ path: filePath, probe: Date.now().toString() })}`);
  check(response.ok(), `Asset GET ${filePath}: ${response.status()} ${await response.text()}`);
  check(response.headers()['content-type']?.startsWith('image/png'), `Expected image/png for ${filePath}`);
  return await response.body();
};
const waitImage = async (dialog, alt) => dialog.getByAltText(alt).evaluate(image => new Promise((resolve, reject) => {
  if (image.complete) return image.naturalWidth ? resolve() : reject(new Error(`Image ${image.alt} failed to load`));
  image.addEventListener('load', resolve, { once: true });
  image.addEventListener('error', () => reject(new Error(`Image ${image.alt} failed to load`)), { once: true });
}));
const selectFile = async (dialog, fileName) => {
  await dialog.getByRole('navigation', { name: 'Skill 文件结构' }).getByRole('button', { name: fileName }).click();
};
const chooseImage = async (dialog, buttonName, image) => {
  const chooser = page.waitForEvent('filechooser');
  await dialog.getByRole('button', { name: buttonName }).click();
  await (await chooser).setFiles(image);
  await dialog.getByRole('group', { name: '待保存图片' }).waitFor();
};
const confirmClick = async button => {
  page.once('dialog', dialog => dialog.accept());
  await button.click();
};
const dismissClick = async button => {
  page.once('dialog', dialog => dialog.dismiss());
  await button.click();
};
const mutationResponse = (method, endpoint) => page.waitForResponse(response =>
  response.request().method() === method && new URL(response.url()).pathname === endpoint, { timeout: 15000 });
const assertFrozen = (snapshot, initial, skillId) => {
  const file = relative => snapshot.files.find(entry => entry.path === `skills/${relative}/${skillId}/assets/reference.png`);
  check(file('versions')?.sha256 === initial, 'Frozen version reference.png changed');
};
const openMainSkill = async () => {
  await page.goto(base + '/skills');
  await page.getByRole('heading', { name: 'Skill 库' }).waitFor();
  await page.getByRole('button', { name: 'asset-live-main' }).click();
  await page.getByRole('dialog').getByRole('heading', { name: 'asset-live-main' }).waitFor();
  check(Boolean(await mainId()), 'Fixture target Skill missing');
};

try {
  await openMainSkill();
  if (mode === '--baseline') {
    await waitImage(page.getByRole('dialog'), 'assets/reference.png');
    await capture('baseline-desktop.png');
    await page.setViewportSize({ width: 390, height: 844 });
    await capture('baseline-mobile.png');
    mark({ baseline: 'captured old compiled UI; no mutations' });
  } else if (mode === '--visual') {
    const before = await evidence();
    const id = before.setup.skill_ids['asset-live-main'];
    const originalHash = inputHashes[0];
    const originalFiles = await skillFiles(id);
    const dialog = page.getByRole('dialog');
    check(before.files.find(file => file.path === `skills/working/${id}/assets/reference.png`)?.sha256 === originalHash,
      'Fresh visual fixture has wrong reference bytes');
    await selectFile(dialog, 'reference.png');
    await waitImage(dialog, 'assets/reference.png');
    await capture('asset-controls-desktop.png');
    await page.setViewportSize({ width: 390, height: 844 });
    await selectFile(dialog, 'unused.png');
    await selectFile(dialog, 'reference.png');
    await waitImage(dialog, 'assets/reference.png');
    const targets = {};
    for (const label of ['添加图片', '替换图片', '删除图片']) {
      const button = dialog.getByRole('button', { name: label });
      const measurements = await button.evaluate(element => {
        const box = element.getBoundingClientRect();
        return { width: box.width, height: box.height, fontSize: getComputedStyle(element).fontSize,
          hasSvg: Boolean(element.querySelector('svg')) };
      });
      check(measurements.height >= 44, `${label} mobile touch target under 44px: ${JSON.stringify(measurements)}`);
      targets[label] = measurements;
    }
    check(targets['添加图片'].hasSvg, 'Add image button lacks an SVG icon');
    await dialog.locator('.skill-inspector-sidebar').evaluate(element => { element.scrollTop = 0; });
    await capture('asset-controls-mobile.png');
    await selectFile(dialog, 'unused.png');
    await selectFile(dialog, 'reference.png');
    await capture('asset-file-list-mobile.png', 'asset-file-list-mobile.png');
    mark({ mobileTargets: targets });
    await chooseImage(dialog, '替换图片', replacementPng);
    check(await dialog.getByRole('group', { name: '待保存图片' }).getByText(path.basename(replacementPng), { exact: false }).count() > 0,
      'Replacement draft does not preview the selected file');
    await dialog.getByRole('group', { name: '待保存图片' }).getByRole('button', { name: '取消' }).click();
    check(await dialog.getByRole('group', { name: '待保存图片' }).count() === 0, 'Cancelled replacement draft remains');
    await dismissClick(dialog.getByRole('button', { name: '删除图片' }));
    await dismissClick(dialog.getByRole('button', { name: '删除这个 Skill' }));
    await page.reload();
    await page.getByRole('button', { name: 'asset-live-main' }).click();
    await selectFile(dialog, 'reference.png');
    await waitImage(dialog, 'assets/reference.png');
    const after = await evidence();
    const afterFiles = await skillFiles(id);
    check(afterFiles.digest === originalFiles.digest, 'Cancel/refresh changed Skill digest');
    check(after.files.find(file => file.path === `skills/working/${id}/assets/reference.png`)?.sha256 === originalHash,
      'Cancel/refresh changed reference image');
    check(hash(await assetBytes(id, 'assets/reference.png')) === originalHash, 'Visual probe changed HTTP image bytes');
    check((await get('/api/producer-skills')).items.some(skill => skill.id === id), 'Cancelled Skill deletion removed it');
    check(after.series.length === before.series.length && after.runs_count === 0 && after.forbidden_calls.length === 0,
      'Visual probe changed business state');
    check(report.network.length === 0 && report.pageErrors.length === 0,
      `Visual probe made writes or page errors: ${JSON.stringify(report.network)} ${JSON.stringify(report.pageErrors)}`);
    mark({ result: 'visual passed', cancelledReplacement: true, cancelledImageDelete: true,
      cancelledSkillDelete: true, noWrites: true, refresh: true });
  } else {
    const before = await evidence();
    const id = before.setup.skill_ids['asset-live-main'];
    const unusedId = before.setup.skill_ids['asset-live-unused'];
    const otherId = before.setup.skill_ids['asset-live-other'];
    const originalHash = inputHashes[0], replacementHash = inputHashes[1];
    check(before.runs_count === 0 && before.forbidden_calls.length === 0, 'Fixture is already contaminated');
    check(before.series.length === 2 && before.series.some(item => item.skill_name === id && item.creator_id === 'skill-assets-a'),
      'Fixture binding is wrong');
    check(before.files.find(file => file.path === `skills/working/${id}/assets/reference.png`)?.sha256 === originalHash,
      'Fixture reference bytes differ from source');
    assertFrozen(before, originalHash, id);
    const frozenMarkdownHash = before.files.find(file => file.path === `skills/versions/${id}/SKILL.md`)?.sha256;
    check(frozenMarkdownHash && frozenMarkdownHash === hash(readFileSync(path.join(before.setup.root, 'sources/asset-live-main/SKILL.md'))),
      'Initial frozen Markdown differs from original source');
    report.initialHashes = { originalImage: originalHash, frozenMarkdown: frozenMarkdownHash };
    const otherBefore = before.files.find(file => file.path === `skills/working/${otherId}/SKILL.md`)?.sha256;
    const dialog = page.getByRole('dialog');

    await chooseImage(dialog, '添加图片', replacementPng);
    await dialog.getByRole('group', { name: '待保存图片' }).getByLabel('保存路径').fill('assets/added.png');
    report.requests.push({ operation: 'add image', path: 'assets/added.png', sha256: replacementHash });
    let pending = mutationResponse('PUT', `/api/producer-skills/${encodeURIComponent(id)}/assets`);
    await dialog.getByRole('group', { name: '待保存图片' }).getByRole('button', { name: '保存图片' }).click();
    check((await pending).status() === 200, 'Add image PUT failed');
    await selectFile(dialog, 'added.png');
    await waitImage(dialog, 'assets/added.png');
    check(hash(await assetBytes(id, 'assets/added.png')) === replacementHash, 'Added image HTTP bytes differ');
    mark({ addedImage: 'assets/added.png', sha256: replacementHash, http: 200 });

    pending = mutationResponse('DELETE', `/api/producer-skills/${encodeURIComponent(id)}/assets`);
    report.requests.push({ operation: 'delete image', path: 'assets/added.png' });
    await confirmClick(dialog.getByRole('button', { name: '删除图片' }));
    check((await pending).status() === 200, 'Delete unused image failed');
    check(!(await skillFiles(id)).files.some(file => file.path === 'assets/added.png'), 'Deleted image remains in file tree');
    mark({ deletedUnusedImage: 'assets/added.png' });

    await selectFile(dialog, 'reference.png');
    await chooseImage(dialog, '替换图片', replacementPng);
    report.requests.push({ operation: 'replace image', path: 'assets/reference.png', sha256: replacementHash });
    pending = mutationResponse('PUT', `/api/producer-skills/${encodeURIComponent(id)}/assets`);
    await dialog.getByRole('group', { name: '待保存图片' }).getByRole('button', { name: '保存图片' }).click();
    check((await pending).status() === 200, 'Replace image PUT failed');
    await waitImage(dialog, 'assets/reference.png');
    check(hash(await assetBytes(id, 'assets/reference.png')) === replacementHash, 'Replaced image HTTP bytes differ');
    check((await evidence()).files.find(file => file.path === `skills/working/${id}/assets/reference.png`)?.sha256 === replacementHash,
      'Replaced image disk bytes differ');
    await capture('asset-controls-desktop.png');
    await page.setViewportSize({ width: 390, height: 844 });
    await capture('asset-controls-mobile.png');
    await page.setViewportSize({ width: 1440, height: 900 });
    await dialog.getByRole('button', { name: '关闭 Skill 详情' }).click();
    await page.getByRole('button', { name: 'asset-live-main' }).click();
    await selectFile(dialog, 'reference.png');
    await waitImage(dialog, 'assets/reference.png');
    check(hash(await assetBytes(id, 'assets/reference.png')) === replacementHash, 'Close/reopen lost replacement');
    await page.reload();
    await page.getByRole('button', { name: 'asset-live-main' }).click();
    await selectFile(dialog, 'reference.png');
    await waitImage(dialog, 'assets/reference.png');
    check(hash(await assetBytes(id, 'assets/reference.png')) === replacementHash, 'Refresh lost replacement');
    mark({ replacedImage: 'assets/reference.png', sha256: replacementHash, closeReopen: true, refresh: true });

    await dialog.getByRole('button', { name: '关闭 Skill 详情' }).click();
    await page.goto(base + '/?creator=skill-assets-a&series=skill-assets-series-a');
    await page.getByRole('button', { name: '打开账号对话' }).click();
    const panel = page.locator('.account-chat-panel');
    await panel.getByRole('heading', { name: '[隔离验证] 主账号' }).waitFor();
    await panel.locator('input[type=file][aria-label="选择图片文件"]').setInputFiles(originalPng);
    await panel.getByRole('img', { name: path.basename(originalPng) }).waitFor();
    const instruction = '请把当前账号「参考图栏目」绑定的 asset-live-main Skill 中 assets/reference.png 替换成我本条消息附的图片，并在 SKILL.md 最后加一行「LIVE_ASSET_RULE: 使用新的参考图时先核对来源。」。请先读取这个 Skill 的文件和摘要，再用当前附件替换图片，然后以新摘要保存完整 SKILL.md；不要改其他 Skill、不要试产、生产或发布。';
    report.requests.push({ operation: 'account chat', text: instruction, attachmentSha256: originalHash });
    const input = panel.getByRole('textbox', { name: '给 Agent 的消息' });
    await input.fill(instruction);
    pending = page.waitForResponse(response => response.request().method() === 'POST' &&
      /\/api\/agent\/sessions\/[^/]+\/turns$/.test(new URL(response.url()).pathname), { timeout: 15000 });
    await panel.getByRole('button', { name: '发送 ↑' }).click();
    const acceptedResponse = await pending;
    check(acceptedResponse.status() === 202, `Chat submit failed: ${acceptedResponse.status()} ${await acceptedResponse.text()}`);
    const accepted = await acceptedResponse.json();
    let view;
    for (let tries = 0; tries < 360; tries++) {
      view = await get(`/api/agent/sessions/${accepted.id}`);
      if (view.status !== 'running') break;
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    report.turns.push({ sessionId: accepted.id, status: view?.status, entries: view?.entries, error: view?.error });
    check(view?.status === 'idle', `Chat did not finish cleanly: ${view?.status} ${view?.error ?? ''}`);
    const toolEntries = view.entries.filter(entry => entry.kind === 'tool');
    const calls = toolEntries.map(entry => entry.name);
    const toolErrors = toolEntries.filter(entry => entry.status !== 'done');
    report.toolErrorsCount = toolErrors.length;
    check(toolErrors.length === 0, `Chat tool errors: ${toolErrors.map(item => item.name).join(', ')}`);
    check(calls.filter(name => name === 'update_producer_skill_file').length >= 2,
      `Expected image + Markdown edits via same tool: ${calls.join(', ')}`);
    check(!calls.some(name => ['extract_skill_from_images', 'extract_skills_from_artifact', 'start_content_run',
      'research_series_topics', 'route_and_answer', 'compose_series'].includes(name)), `Forbidden tool used: ${calls.join(', ')}`);
    const afterChat = await evidence();
    check(afterChat.files.find(file => file.path === `skills/working/${id}/assets/reference.png`)?.sha256 === originalHash,
      'Chat did not restore attached image bytes');
    const textResponse = await page.request.get(`${base}/api/producer-skills/${encodeURIComponent(id)}/files/content?path=SKILL.md`);
    check(textResponse.ok(), `Chat SKILL.md read: ${textResponse.status()}`);
    const editedMarkdown = (await textResponse.json()).content;
    check(editedMarkdown.includes('LIVE_ASSET_RULE: 使用新的参考图时先核对来源。'), 'Chat did not append the exact rule');
    check(editedMarkdown.includes('assets/reference.png'), 'Chat removed the existing reference');
    assertFrozen(afterChat, originalHash, id);
    mark({ agentCalls: calls, toolErrors: toolErrors.length, chatImageSha256: originalHash, ruleSaved: true });

    await openMainSkill();
    await selectFile(dialog, 'reference.png');
    pending = mutationResponse('DELETE', `/api/producer-skills/${encodeURIComponent(id)}/assets`);
    await confirmClick(dialog.getByRole('button', { name: '删除图片' }));
    const blockedAsset = await pending;
    check(blockedAsset.status() === 409, `Referenced asset deletion should be blocked: ${blockedAsset.status()}`);
    await dialog.getByRole('alert').getByText(/引用它的文件|SKILL\.md/).waitFor();
    check(hash(await assetBytes(id, 'assets/reference.png')) === originalHash, 'Blocked deletion mutated image');
    mark({ referencedDelete: 409, response: await blockedAsset.json() });
    await selectFile(dialog, 'SKILL.md');
    await dialog.getByRole('button', { name: '编辑', exact: true }).click();
    const editor = dialog.getByRole('textbox', { name: '编辑 SKILL.md' });
    const withoutReference = (await editor.inputValue()).replace('![原始参考图](assets/reference.png)', '原始参考图引用已撤下。');
    check(withoutReference !== await editor.inputValue(), 'Could not find exact image reference to remove');
    await editor.fill(withoutReference);
    pending = mutationResponse('PUT', `/api/producer-skills/${encodeURIComponent(id)}/files/content`);
    await dialog.getByRole('button', { name: '保存文件' }).click();
    check((await pending).status() === 200, 'Removing image reference from Markdown failed');
    await selectFile(dialog, 'reference.png');
    pending = mutationResponse('DELETE', `/api/producer-skills/${encodeURIComponent(id)}/assets`);
    await confirmClick(dialog.getByRole('button', { name: '删除图片' }));
    check((await pending).status() === 200, 'Unreferenced image deletion failed');
    check(!(await skillFiles(id)).files.some(file => file.path === 'assets/reference.png'), 'Reference image remains after delete');
    mark({ referenceRemovedThenImageDeleted: true });

    pending = mutationResponse('DELETE', `/api/producer-skills/${encodeURIComponent(id)}`);
    await confirmClick(dialog.getByRole('button', { name: '删除这个 Skill' }));
    const blockedSkill = await pending;
    check(blockedSkill.status() === 409, `Bound Skill delete should be blocked: ${blockedSkill.status()}`);
    await dialog.getByRole('alert').getByText(/参考图栏目/).waitFor();
    mark({ boundSkillDelete: 409, response: await blockedSkill.json() });
    await dialog.getByRole('button', { name: '关闭 Skill 详情' }).click();
    await page.getByRole('button', { name: 'asset-live-unused' }).click();
    pending = mutationResponse('DELETE', `/api/producer-skills/${encodeURIComponent(unusedId)}`);
    await confirmClick(dialog.getByRole('button', { name: '删除这个 Skill' }));
    check((await pending).status() === 200, 'Unused Skill deletion failed');
    check(!(await get('/api/producer-skills')).items.some(skill => skill.id === unusedId), 'Deleted Skill remains in catalog');
    await page.reload();
    check(await page.getByRole('button', { name: 'asset-live-unused' }).count() === 0, 'Deleted Skill reappeared after refresh');
    mark({ unusedSkillDeleted: unusedId, refresh: true });

    await page.getByRole('button', { name: 'asset-live-main' }).click();
    await dialog.getByRole('button', { name: '加入组合' }).click();
    const composer = page.locator('form.composer-form');
    check(await composer.getByLabel('栏目定位').count() === 0 && await composer.getByLabel('目标受众').count() === 0,
      'Simple column creator still requires positioning/audience fields');
    await composer.getByLabel('栏目名称').fill('只填名称的新栏目');
    await composer.getByLabel('归属账号').selectOption('skill-assets-a');
    pending = mutationResponse('POST', '/api/series');
    await composer.getByRole('button', { name: '创建栏目' }).click();
    check((await pending).status() === 201, 'Simple column creation failed');
    const final = await evidence();
    check(final.series.some(item => item.creator_id === 'skill-assets-a' && item.skill_name === id &&
      item.name === '只填名称的新栏目' && !item.description && !item.audience), 'Created column did not persist as name-only');
    check(final.files.find(file => file.path === `skills/working/${otherId}/SKILL.md`)?.sha256 === otherBefore,
      'Other account Skill changed');
    assertFrozen(final, originalHash, id);
    check(final.files.find(file => file.path === `skills/versions/${id}/SKILL.md`)?.sha256 === frozenMarkdownHash,
      'Frozen version SKILL.md changed');
    check(hash(readFileSync(path.join(final.setup.root, 'sources/asset-live-main/SKILL.md'))) === frozenMarkdownHash &&
      hash(readFileSync(path.join(final.setup.root, 'sources/asset-live-main/assets/reference.png'))) === originalHash &&
      hash(readFileSync(originalPng)) === originalHash && hash(readFileSync(replacementPng)) === replacementHash,
      'Original source or supplied PNG changed');
    check(final.runs_count === 0 && final.forbidden_calls.length === 0, 'Forbidden boundary or production run was reached');
    check(report.pageErrors.length === 0, `Page errors: ${report.pageErrors.join(', ')}`);
    mark({ result: 'passed', columns: final.series.length, frozenCopyUnchanged: true,
      otherAccountSkillUnchanged: true, toolErrors: report.toolErrorsCount, forbiddenCalls: final.forbidden_calls.length });
  }
} catch (error) {
  report.failure = String(error?.stack ?? error);
  console.error(report.failure);
  try { await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }); } catch { /* preserve evidence */ }
  process.exitCode = 1;
} finally {
  try { report.finalEvidence = await evidence(); } catch (error) { report.evidenceError = String(error); }
  writeFileSync(path.join(output, 'browser-report.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
