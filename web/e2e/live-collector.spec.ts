import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { expectEvalEvidence, expectProductionRecord, expectRenderedReply, type EvalViewEvidence } from "../live-eval/ui-assertions";

// Deterministic collector regression. Controlled documents test delivery and
// failure detection; none of these results are paid-model task scores.
async function chat(page: Page, text: string, links: Array<{ url: string; label: string }> = []) {
  const doc = { id: "collector", title: "采集器回归", version: 1, status: "idle", error: null,
    scope_kind: "overview", creator_id: null, updated_at: "2026-10-11T00:00:00Z", has_older: false,
    entries: [{ kind: "assistant", text, complete: true, links }] };
  await page.route("**/api/agent/sessions**", route => route.fulfill({ json:
    new URL(route.request().url()).pathname.endsWith("/collector") ? doc : { items: [doc] } }));
  await page.goto("/agent?chat=collector");
}

async function evalFixture(page: Page, request: APIRequestContext) {
  const seeded = await request.post("/test/eval-runs", { data: {} });
  expect(seeded.ok()).toBe(true);
  const { run_id: runId } = await seeded.json();
  const report = await (await request.get(`/api/eval/runs/${runId}`)).json();
  const query = "实际用户输入；不是题目定义。".repeat(70);
  const context = { messages: [{ role: "system", content: "实际请求系统上下文。".repeat(70) }, { role: "user", content: query }], tools: [] };
  const requests = [{ trace_request_id: "collector-real-request", method: "complete", context,
    response: { content: "受控公开输出", tool_calls: [], finish_reason: "stop" } }];
  const before = { database: { creators: [{ id: "creator-a", display_name: "执行前名称" }],
    series: [{ id: "series-a", creator_id: "creator-a" }], topics: [] }, files: {}, metadata: { files_complete: true } };
  const after = structuredClone(before);
  after.database.creators[0].display_name = "相同行数，但完整行已变化";
  const oracle = { expected_row_counts: { creators: 1, series: 1, topics: 0 } };
  const answer = "原始答复正文，必须真正加载，不能只看到文件名。".repeat(40) + "原文末尾标记。";
  const evidence: EvalViewEvidence = { userQueries: [query], requests, before, after, oracle, answer };
  const docs: Record<string, unknown> = { "messages.json": [{ role: "user", content: query }, { role: "assistant", content: answer }],
    "requests.json": requests, "trace.json": [{ request_id: "collector-real-request", event: "started", request_kind: "main" }],
    "before.json": before, "after.json": after, "oracle.json": oracle, "answer.txt": answer,
    "execution.json": { status: "idle" } };
  await page.route(`**/api/eval/runs/${runId}`, route => route.fulfill({ json: { ...report, execution_mode: "controlled",
    evidence_files: Object.keys(docs).map(name => ({ name, label: name })), checks: [] } }));
  return { runId, evidence, docs };
}

test("collector refresh compares the same innerText for a Markdown table", async ({ page }) => {
  const writes: string[] = [];
  page.on("request", event => { if (event.method() !== "GET") writes.push(event.url()); });
  await chat(page, "| 栏目 | 状态 |\n| --- | --- |\n| 同义词 | 待选 |\n\n已读取真实目录。");
  const reply = page.locator(".chat-answer").last();
  await expect(reply.getByRole("table")).toBeVisible();
  const rendered = await reply.innerText();
  expect(await reply.textContent()).not.toBe(rendered);
  await page.reload();
  await expectRenderedReply(reply, rendered);
  expect(writes).toEqual([]);
});

test("collector waits for evidence bodies, verifies DB content, opens answer and rejects wrong answer", async ({ page, request }) => {
  const fixture = await evalFixture(page, request);
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  let gated = true;
  const writes: string[] = [];
  page.on("request", event => { if (event.method() !== "GET") writes.push(event.url()); });
  await page.route(`**/api/eval/runs/${fixture.runId}/evidence?*`, async route => {
    const name = new URL(route.request().url()).searchParams.get("name")!;
    if (gated && ["requests.json", "before.json", "after.json"].includes(name)) await held;
    await route.fulfill({ json: { name, content: fixture.docs[name], format: name.endsWith(".txt") ? "text" : "json" } });
  });
  await page.goto(`/eval?case=E01&run=${fixture.runId}`);
  await expect(page.getByRole("heading", { name: "运行结果" })).toBeVisible();
  const chain = page.getByRole("region", { name: "执行链路", exact: true });
  await expect(chain.getByRole("status").last()).toBeVisible();
  let completed = false;
  const verification = expectEvalEvidence(page, fixture.evidence).then(value => { completed = true; return value; });
  // Headings are already present; the actual evidence is deliberately held.
  expect(completed).toBe(false);
  expect(await chain.locator("tbody tr").count()).toBe(0);
  gated = false;
  release();
  const proof = await verification;
  expect(proof.model_requests_verified).toBe(1);
  expect(proof.evidence_files_opened).toEqual(["before.json", "after.json", "answer.txt"]);
  await expect(chain.getByRole("button", { name: "账号：查看完整行差异", exact: true })).toBeVisible();
  await page.reload();
  await expectEvalEvidence(page, fixture.evidence);
  fixture.docs["answer.txt"] = "错误文件正文：页面标题与其他证据仍然正确。";
  await page.reload();
  await expect(expectEvalEvidence(page, fixture.evidence, 1_500)).rejects.toThrow(/answer\.txt must load/);
  expect(writes).toEqual([]);
});

test("collector real Run click checks status and record identity, not just matching title", async ({ page, request }) => {
  async function create(path: string, data: object) {
    const response = await request.post(path, { data });
    expect(response.ok(), await response.text()).toBe(true);
    return response.json();
  }
  const creator = await create("/api/creators", { display_name: "生产入口采集回归" });
  const series = await create(`/api/creators/${creator.id}/series`, { name: "入口状态隔离栏目" });
  const queued = await create(`/api/series/${series.id}/queue`, { request_id: crypto.randomUUID(), topics: [{ title: "状态采集器测试选题" }] });
  const run = await create("/api/runs", { topic_id: queued.topic_ids[0] });
  expect(run.status).toBe("queued");
  const writes: string[] = [];
  page.on("request", event => { if (event.method() !== "GET") writes.push(event.url()); });
  await chat(page, "已提供任务入口；本例不执行生产。", [{ url: `/runs/${run.id}`, label: "查看内容" }]);
  await page.getByRole("navigation", { name: "本轮任务入口" }).getByRole("link", { name: "查看内容", exact: true }).click();
  await expect(page).toHaveURL(`/runs/${run.id}`);
  await expect(page.getByRole("heading", { name: run.topic_title, exact: true })).toBeVisible();
  await expectProductionRecord(page, run);
  await page.route(`**/api/runs/${run.id}`, route => route.fulfill({ json: { ...run, status: "failed", error_message: "受控错误投影" } }));
  await page.reload();
  await expect(page.getByRole("heading", { name: run.topic_title, exact: true })).toBeVisible();
  await expect(expectProductionRecord(page, run)).rejects.toThrow(/status-queued/);
  expect((await (await request.get(`/api/runs/${run.id}`)).json()).status).toBe("queued");
  expect(writes).toEqual([]);
});
