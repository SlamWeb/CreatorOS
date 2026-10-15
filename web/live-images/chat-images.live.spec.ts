import { expect, test, type Page, type APIRequestContext, type TestInfo } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

const root = process.env.CREATOROS_IMAGES_ROOT ?? path.resolve(import.meta.dirname, "../../tmp/live-chat-images-20261015");
const setup = JSON.parse(readFileSync(path.join(root, "setup.json"), "utf8"));
const base = "/api/agent/sessions";

test("real clipboard images → DeepSeek vision → local Skill edit → awaiting-review import", async ({ page, request }, info) => {
  test.skip(!!process.env.CREATOROS_IMAGES_REVIEW_REPORT || !!process.env.CREATOROS_IMAGES_EXTRACT_SESSION, "Existing evidence is checked without repeating paid turns.");
  const evidence: Record<string, unknown> = { schema: 1, real_model: true, isolated_root: root, turns: [] };
  const reportPath = path.join(root, `browser-evidence-${Date.now()}.json`);
  const persist = () => writeFileSync(reportPath, JSON.stringify(evidence, null, 2));
  const initialSkill = await (await request.get(`/api/producer-skills/${setup.skill_id}/files/content?path=SKILL.md`)).json();
  evidence.skill_before = initialSkill;
  const resume = process.env.CREATOROS_IMAGES_RESUME_FIRST_SESSION;
  await page.goto(`/agent?creator=${setup.creator_id}${resume ? `&chat=${resume}` : ""}`);
  const input = page.getByRole("textbox", { name: "给 Agent 的消息" });
  for (const name of resume ? [] : ["reference.png", "result.png"]) {
    const bytes = readFileSync(path.join(root, name)).toString("base64");
    await page.evaluate(async data => {
      const binary = atob(data); const buffer = Uint8Array.from(binary, char => char.charCodeAt(0));
      await navigator.clipboard.write([new ClipboardItem({ "image/png": new Blob([buffer], { type: "image/png" }) })]);
    }, bytes);
    await input.focus(); await page.keyboard.press("Control+v");
    await expect(page.getByLabel("待发送图片").getByRole("img")).toHaveCount(name === "reference.png" ? 1 : 2);
  }
  const sid = new URL(page.url()).searchParams.get("chat")!;
  evidence.session_id = sid; persist();
  if (!resume) await expect.poll(async () => (await page.getByLabel("待发送图片").getByRole("img").evaluateAll(nodes => nodes.every(node => (node as HTMLImageElement).naturalWidth > 0)))).toBe(true);
  const doc1 = resume ? await (await request.get(`${base}/${sid}`)).json()
    : await sendTurn(page, request, sid, "请实际看我上传的两张图片，分别告诉我图中的英文字母和数字，以及主要色块颜色，简短比较差异。不要修改任何数据。", info, 1);
  if (resume) expect(doc1.entries.filter((entry: { kind: string }) => entry.kind === "user")).toHaveLength(1);
  (evidence.turns as unknown[]).push(doc1); persist();
  const answer = doc1.entries.filter((entry: { kind: string }) => entry.kind === "assistant").map((entry: { text?: string }) => entry.text ?? "").join("\n");
  expect(answer).toMatch(/ALPHA\s*73/); expect(answer).toMatch(/BETA\s*29/); expect(answer).toMatch(/红/); expect(answer).toMatch(/蓝/);
  await page.reload();
  await expect(page.getByLabel("消息图片").getByRole("img")).toHaveCount(2);
  await page.screenshot({ path: info.outputPath("vision-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: info.outputPath("vision-mobile.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.setViewportSize({ width: 1440, height: 900 });
  const doc2 = await sendTurn(page, request, sid, `请修改当前账号栏目绑定的本地 Skill「image-lab-skill」（Skill ID：${setup.skill_id}）。先用 read_image 实际查看这个 Skill 的 assets 参考图片，再读取 SKILL.md，只在正文末尾增加这一句：“每张图底部保留清晰的英文标记。”，其他内容不要改变。请直接保存这项修改，不要委托 Codex，不要生产或发布。`, info, 2);
  (evidence.turns as unknown[]).push(doc2); persist();
  const edited = await (await request.get(`/api/producer-skills/${setup.skill_id}/files/content?path=SKILL.md`)).json();
  evidence.skill_after = edited; persist();
  expect(edited.content).toContain("每张图底部保留清晰的英文标记。");
  expect(edited.content.replace("每张图底部保留清晰的英文标记。", "").trim()).toBe(initialSkill.content.trim());
  expect(doc2.entries.some((entry: { kind: string; name?: string; status?: string }) => entry.kind === "tool" && entry.name === "read_image" && entry.status === "done")).toBe(true);
  const doc3 = await sendTurn(page, request, sid, "把我最初上传的第二张蓝色色块图作为现成作品，导入当前账号的图片验证栏目，标题严格用“蓝色色块验证”，正文严格用“这是人工上传的成品”。只导入这一张，成为待人工验收的内容，不要重新生图、不要批准、不要发布。", info, 3);
  (evidence.turns as unknown[]).push(doc3); persist();
  const links = doc3.entries.filter((entry: { kind: string }) => entry.kind === "assistant").flatMap((entry: { links?: Array<{ url: string; label: string }> }) => entry.links ?? []);
  const runLink = links.find((link: { url: string }) => /^\/runs\//.test(link.url));
  expect(runLink).toBeTruthy();
  const run = await (await request.get(`/api${runLink.url}`)).json();
  evidence.imported_run = run; persist();
  expect(run.status).toBe("awaiting_approval");
  const delivery = page.getByRole("navigation", { name: "本轮任务入口" }).last();
  await delivery.getByRole("link").filter({ hasText: runLink.label }).click();
  await expect(page).toHaveURL(new RegExp(runLink.url));
  await expect(page.getByRole("heading", { name: "蓝色色块验证", level: 1, exact: true })).toBeVisible();
  await expect.poll(async () => page.locator(".carousel-stage img").evaluateAll(nodes => nodes.some(node => (node as HTMLImageElement).naturalWidth > 0))).toBe(true);
  await page.screenshot({ path: info.outputPath("import-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: info.outputPath("import-mobile.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  evidence.completed = true; persist();
});

test("read-only review of completed real image conversation and imported artifact", async ({ page, request }, info) => {
  test.skip(!process.env.CREATOROS_IMAGES_REVIEW_REPORT, "Explicit evidence report required; no model rerun.");
  const evidence = JSON.parse(readFileSync(process.env.CREATOROS_IMAGES_REVIEW_REPORT!, "utf8"));
  const sid = evidence.session_id;
  const before = await (await request.get(`${base}/${sid}`)).json();
  const runId = evidence.imported_run.id;
  let writes = 0;
  page.on("request", req => { if (!["GET", "HEAD"].includes(req.method())) writes++; });
  await page.goto(`/agent?creator=${setup.creator_id}&chat=${sid}`);
  await expect(page.getByLabel("消息图片").getByRole("img")).toHaveCount(2);
  await page.getByRole("button", { name: "查看回复 Trace" }).first().click();
  const trace = page.getByRole("dialog", { name: "回复 Trace" });
  // The model may explicitly re-read an attachment; Trace must retain those
  // actual message blocks instead of pretending there was exactly one copy.
  const originalImages = before.entries.find((entry: { kind: string }) => entry.kind === "user").attachments;
  await expect.poll(async () => {
    const sources = await trace.getByRole("img").evaluateAll(nodes => nodes.map(node => new URL((node as HTMLImageElement).src).pathname));
    return originalImages.every((image: { url: string }) => sources.includes(image.url));
  }).toBe(true);
  await page.getByRole("button", { name: "关闭 Trace" }).click();
  const delivery = page.getByRole("navigation", { name: "本轮任务入口" }).last();
  await delivery.getByRole("link").click();
  await expect(page).toHaveURL(new RegExp(`/runs/${runId}$`));
  await expect(page.getByRole("heading", { name: "蓝色色块验证", level: 1, exact: true })).toBeVisible();
  await expect(page.getByText("待批准", { exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "发布文案" })).toContainText("这是人工上传的成品");
  await expect.poll(async () => page.locator(".carousel-stage img").evaluateAll(nodes => nodes.some(node => (node as HTMLImageElement).naturalWidth > 0))).toBe(true);
  await page.screenshot({ path: info.outputPath("import-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: info.outputPath("import-mobile.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.reload();
  await expect(page.getByRole("heading", { name: "蓝色色块验证", level: 1, exact: true })).toBeVisible();
  const after = await (await request.get(`${base}/${sid}`)).json();
  expect(after.version).toBe(before.version);
  expect(after.entries).toEqual(before.entries);
  const run = await (await request.get(`/api/runs/${runId}`)).json();
  expect(run.status).toBe("awaiting_approval"); expect(run.publication).toBeNull();
  expect(run.revisions).toHaveLength(1); expect(run.revisions[0].cards).toHaveLength(1);
  expect(writes).toBe(0);
  writeFileSync(info.outputPath("readonly-verification.json"), JSON.stringify({ session_id: sid, run_id: runId, status: run.status, writes, verified: true }, null, 2));
});

test("existing chat images create one real Codex Skill draft without saving or trial production", async ({ page, request }, info) => {
  test.setTimeout(720_000);
  const sid = process.env.CREATOROS_IMAGES_EXTRACT_SESSION;
  test.skip(!sid, "Explicit existing isolated session required; paid Codex extraction.");
  const report: Record<string, unknown> = { session_id: sid, real_model: true, query: "将我最初上传的两张图片提炼成一个完整的单 Skill，保留色块加底部英文标签的可复用形式，用于制作新内容。先出草稿，不要入库，不要试生产，不要生图。" };
  const persist = () => writeFileSync(info.outputPath("extraction-evidence.json"), JSON.stringify(report, null, 2));
  await page.goto(`/agent?creator=${setup.creator_id}&chat=${sid}`);
  const doc = process.env.CREATOROS_IMAGES_EXTRACTION_JOB ? await (await request.get(`${base}/${sid}`)).json()
    : await sendTurn(page, request, sid!, String(report.query), info, 4);
  report.chat = doc; persist();
  const link = doc.entries.filter((entry: { kind: string }) => entry.kind === "assistant")
    .flatMap((entry: { links?: Array<{ url: string; label: string }> }) => entry.links ?? [])
    .findLast((item: { url: string }) => /^\/skills\?extraction=[a-f0-9]{64}$/.test(item.url));
  expect(link).toBeTruthy();
  const jobId = new URL(link.url, "http://localhost").searchParams.get("extraction")!;
  report.job_id = jobId; persist();
  await page.getByRole("navigation", { name: "本轮任务入口" }).last().getByRole("link", { name: link.label, exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/skills\\?extraction=${jobId}$`));
  let job: any;
  await expect.poll(async () => {
    job = await (await request.get(`/api/skill-extractions/${jobId}`)).json();
    report.job = job; persist();
    console.log(JSON.stringify({ job_id: jobId, status: job.status, activity: job.progress?.activity }));
    return ["ready", "failed", "cancelled"].includes(job.status);
  }, { timeout: 600_000, intervals: [30_000] }).toBe(true);
  expect(job.status).toBe("ready"); expect(job.saved_skills).toEqual([]); expect(job.trials ?? []).toEqual([]);
  expect(job.skills).toHaveLength(1); expect(job.files.filter((file: { kind: string }) => file.kind === "image").length).toBeGreaterThanOrEqual(2);
  await expect(page.getByText("任务状态：待确认")).toBeVisible({ timeout: 15_000 });
  const tree = page.getByRole("navigation", { name: "Skill 文件" });
  await tree.getByRole("button", { name: "SKILL.md", exact: true }).click();
  await expect(page.locator(".extraction-markdown")).toContainText(job.skills[0].name);
  for (const file of job.files.filter((item: { kind: string }) => item.kind === "image")) {
    await tree.getByRole("button", { name: file.path, exact: true }).click();
    await expect.poll(async () => page.locator(".extraction-asset-preview").evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
  }
  await tree.getByRole("button", { name: "SKILL.md", exact: true }).click();
  await page.screenshot({ path: info.outputPath("extraction-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: info.outputPath("extraction-mobile.png"), fullPage: true });
  const layout = await page.evaluate(() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth,
    overflowing: Array.from(document.querySelectorAll("main *")).map(node => ({ tag: node.tagName, class: node.className, width: node.getBoundingClientRect().width, right: node.getBoundingClientRect().right }))
      .filter(node => node.right > innerWidth + 1).slice(0, 20) }));
  writeFileSync(info.outputPath("extraction-layout.json"), JSON.stringify(layout, null, 2));
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`/agent?creator=${setup.creator_id}&chat=${sid}`);
  await page.locator(".agent-transcript").evaluate(node => { node.scrollTop = 0; });
  await page.getByLabel("消息图片").getByRole("img").first().scrollIntoViewIfNeeded();
  await expect.poll(async () => page.getByLabel("消息图片").getByRole("img").evaluateAll(nodes => nodes.every(node => (node as HTMLImageElement).complete && (node as HTMLImageElement).naturalWidth > 0))).toBe(true);
  await page.screenshot({ path: info.outputPath("vision-desktop-loaded.png"), fullPage: true });
  report.verified = true; persist();
});

async function sendTurn(page: Page, request: APIRequestContext, sid: string, query: string, info: TestInfo, index: number) {
  const before = await (await request.get(`${base}/${sid}`)).json();
  await page.getByRole("textbox", { name: "给 Agent 的消息" }).fill(query);
  await page.getByRole("button", { name: "发送 ↑" }).click();
  let doc = before;
  await expect.poll(async () => {
    doc = await (await request.get(`${base}/${sid}`)).json();
    return doc.entries.filter((entry: { kind: string }) => entry.kind === "user").length > before.entries.filter((entry: { kind: string }) => entry.kind === "user").length && doc.status !== "running";
  }, { timeout: 180_000, intervals: [1000, 2000, 3000] }).toBe(true);
  writeFileSync(info.outputPath(`turn-${index}.json`), JSON.stringify({ query, ...doc }, null, 2));
  expect(doc.error).toBeNull();
  await expect(page.getByRole("button", { name: "发送 ↑" })).toBeDisabled();
  return doc;
}
