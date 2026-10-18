/** Opt-in paid frontend probes. No routes are mocked; never run against production. */
import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";

const root = process.env.CREATOROS_LIVE_REFERENCE_ROOT;
test.skip(!root, "Requires an explicit isolated real-model server and quota authorization.");
test.setTimeout(1_200_000);
const setup = root ? JSON.parse(readFileSync(`${root}/setup.json`, "utf8")) : null;

test("real frontend references directly produce one native image without generating candidates", async ({ page, request }, info) => {
  const before = await request.get(`${setup.url}/api/runs`).then(r => r.json());
  expect(before.page.total).toBe(0); // Fresh frozen probe directory, no silent rerun.
  const calls: string[] = [];
  page.on("request", req => { if (req.method() === "POST") calls.push(new URL(req.url()).pathname); });
  await page.goto(`${setup.url}/series/${setup.series_id}`);
  const form = page.locator(".topic-reference-composer");
  if (!await form.isVisible()) await page.getByRole("button", { name: "调研选题", exact: true }).click();
  await form.getByLabel("参考图片", { exact: true }).setInputFiles(setup.references);
  await form.getByLabel("本次偏好").fill("直接用两张参考图的内容制作一张英语辨析知识图：borrow/lend与teach/learn，每组用简短中英文解释并给一个例句。只借鉴内容，呈现沿用栏目Skill，不照搬原图红蓝配色。只生成1张实际图片，一次生图，不自动重画，不发布。");
  await expect(form.getByRole("checkbox", { name: /同时参考呈现/ })).not.toBeChecked();
  await page.screenshot({ path: info.outputPath("live-reference-input.png"), fullPage: true });
  await form.getByRole("button", { name: "直接制作", exact: true }).click();
  await expect(page).toHaveURL(/\/runs\/[^/?]+$/);
  const runId = new URL(page.url()).pathname.split("/").at(-1)!;
  await expect.poll(async () => {
    const data = await request.get(`${setup.url}/api/runs/${runId}`).then(r => r.json());
    console.log(`real_production=${data.status} pages=${data.partial_cards?.length ?? 0}`);
    return data.status;
  }, { timeout: 1_080_000, intervals: [3000, 5000, 10_000] }).not.toMatch(/^(queued|producing|validating)$/);
  const run = await request.get(`${setup.url}/api/runs/${runId}`).then(r => r.json());
  await info.attach("real-run.json", { body: JSON.stringify(run, null, 2), contentType: "application/json" });
  expect(run.status, run.error_message).toBe("awaiting_approval");
  expect(run.revisions[0].cards).toHaveLength(1);
  expect(run.input_snapshot.task_references.images).toHaveLength(2);
  expect(run.revisions[0].attempts).toHaveLength(1);
  expect(calls.some(path => path.endsWith("/topic-research"))).toBe(false);
  expect(run.publication).toBeNull();
  await expect(page.getByRole("button", { name: "下载图片", exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath("live-reference-produced.png"), fullPage: true });
});

test("real frontend account Agent hands both attachments to Codex topic research", async ({ page, request }, info) => {
  const runsBefore = await request.get(`${setup.url}/api/runs`).then(r => r.json());
  const batchesBefore = await request.get(`${setup.url}/api/series/${setup.series_id}/topic-research`).then(r => r.json());
  let sessionId = "";
  page.on("response", async response => {
    if (response.request().method() === "POST" && new URL(response.url()).pathname === "/api/agent/sessions") {
      sessionId = (await response.json()).id;
    }
  });
  await page.goto(`${setup.url}/series/${setup.series_id}`);
  await page.getByRole("button", { name: "打开账号对话", exact: true }).click();
  const panel = page.locator(".account-chat-panel");
  await panel.getByLabel("选择图片文件", { exact: true }).setInputFiles(setup.references);
  await expect(panel.getByLabel("待发送图片").getByRole("img")).toHaveCount(2);
  const originalWords = "请根据上传的两张参考图，为本账号的「参考英语单页」栏目扩展1条新的易混英语词辨析选题。只借鉴内容，不模仿红蓝画风。请把两张原图的attachment_ids和这段完整要求一起交给Codex调研工具，查权威词典来源。只调研并告诉我最终结果，不入队、不生产、不改Skill。";
  await panel.getByLabel("给 Agent 的消息").fill(originalWords);
  const submitted = page.waitForResponse(response => response.request().method() === "POST" && /\/turns$/.test(new URL(response.url()).pathname));
  await panel.getByRole("button", { name: /^发送/ }).click();
  expect((await submitted).status()).toBe(202);
  await expect.poll(() => sessionId, { timeout: 15_000 }).not.toBe("");
  await expect.poll(async () => {
    const doc = await request.get(`${setup.url}/api/agent/sessions/${sessionId}`).then(r => r.json());
    return doc.status;
  }, { timeout: 900_000, intervals: [3000, 5000, 10_000] }).not.toBe("running");
  const doc = await request.get(`${setup.url}/api/agent/sessions/${sessionId}`).then(r => r.json());
  const batches = await request.get(`${setup.url}/api/series/${setup.series_id}/topic-research`).then(r => r.json());
  await info.attach("real-agent-session.json", { body: JSON.stringify(doc, null, 2), contentType: "application/json" });
  expect(doc.status).toBe("idle");
  expect(batches.items).toHaveLength(batchesBefore.items.length + 1);
  const batch = await request.get(`${setup.url}/api/topic-research/${batches.items[0].id}`).then(r => r.json());
  await info.attach("real-research.json", { body: JSON.stringify(batch, null, 2), contentType: "application/json" });
  expect(batch.status, batch.error ?? batch.note).toBe("ready");
  expect(batch.reference_summary.image_count).toBe(2);
  expect(batch.candidates.length).toBeGreaterThan(0);
  if (process.env.CREATOROS_ASSERT_RAW_REFERENCE_TURN) {
    const raw = JSON.parse(readFileSync(`${root}/studio-producer-skills-topic-research/batches/${batch.id}.json`, "utf8"));
    expect(raw.task_references.source_user_text).toBe(originalWords);
    const prompt = readFileSync(`${root}/studio-producer-skills-topic-research/work/${batch.id}/1/research_request.txt`, "utf8");
    expect(prompt).toContain(originalWords);
  }
  const runs = await request.get(`${setup.url}/api/runs`).then(r => r.json());
  expect(runs.page.total).toBe(runsBefore.page.total); // Research never produces or adds a Topic.
  await page.screenshot({ path: info.outputPath("live-reference-agent-result.png"), fullPage: true });
});
