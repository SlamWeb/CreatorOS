import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

type Fixture = { run_id: string; failed_run_id: string; long_text: string };
type Cases = { cases: { id: string; title: string; status: string; run_count: number; definition: { steps: { kind: string; text?: string }[]; manual_checks: string[] } }[] };
type Report = { run_id: string; status: string; auto_status: string; report_digest: string; review: { decision: string; note: string } | null };

async function seed(request: APIRequestContext) {
  const response = await request.post("/test/eval-runs", { data: {} });
  expect(response.ok()).toBe(true);
  return await response.json() as Fixture;
}
async function readReport(request: APIRequestContext, runId: string) {
  const response = await request.get(`/api/eval/runs/${runId}`);
  expect(response.ok()).toBe(true);
  return await response.json() as Report;
}
function watchWrites(page: Page) {
  const writes: string[] = [];
  page.on("request", event => {
    if (new URL(event.url()).pathname.startsWith("/api/") && event.method() !== "GET") writes.push(`${event.method()} ${new URL(event.url()).pathname}`);
  });
  return writes;
}

const caseButton = (page: Page, id: string) => page.getByRole("complementary", { name: "评测题目" }).getByRole("button", { name: new RegExp(id) });
const evidenceBlock = (page: Page) => page.getByRole("region", { name: "原始证据" }).locator(".eval-evidence").filter({ has: page.getByRole("button", { name: /完整请求与工具结果/ }) });

test("real isolated empty eval: twelve not-run cases, four categories and refresh never execute", async ({ page, request }, info) => {
  const result = await request.get("/api/eval");
  expect(result.ok()).toBe(true);
  const dataset = await result.json() as Cases;
  expect(dataset.cases).toHaveLength(12);
  expect(dataset.cases.every(item => item.status === "not_run" && item.run_count === 0)).toBe(true);
  const writes = watchWrites(page);
  await page.goto("/");
  await page.getByRole("navigation", { name: "主导航" }).getByRole("link", { name: "Eval", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Eval", exact: true })).toBeVisible();
  await expect(page.locator(".eval-page summary")).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "主导航" }).getByRole("link", { name: "Eval", exact: true })).toHaveClass(/active/);
  const nav = page.getByRole("navigation", { name: "题目分类" });
  await expect(nav.getByRole("button")).toHaveCount(12);
  for (const category of ["账号边界", "会话持久化", "状态更新", "工具正确性"]) await expect(nav.getByRole("heading", { name: category, exact: true })).toBeVisible();
  await expect(nav.locator(".eval-status.not_run")).toHaveCount(12);
  await expect(nav.locator(".eval-status.passed")).toHaveCount(0);
  await expect(page.getByText("npm --prefix web run eval:live -- --project E01", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /启动|开始执行|运行评测/ })).toHaveCount(0);
  const definition = page.locator(".eval-case-definition");
  const firstCase = dataset.cases.find(item => item.id === "E01")!;
  await expect(definition.getByText(firstCase.definition.steps.find(step => step.kind === "user")!.text!, { exact: true })).toBeVisible();
  await expect(definition.getByText(firstCase.definition.manual_checks[0], { exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: /人工检查标准|程序检查标准/ })).toHaveCount(0);
  await page.screenshot({ path: info.outputPath("eval-definition-1440.png"), fullPage: true });
  await page.screenshot({ path: info.outputPath("eval-empty-1440.png"), fullPage: true });
  await caseButton(page, "E02").click();
  const secondCase = dataset.cases.find(item => item.id === "E02")!;
  await expect(definition.getByText(secondCase.definition.steps.find(step => step.kind === "user")!.text!, { exact: true })).toBeVisible();
  await expect(page.getByText("npm --prefix web run eval:live -- --project E02", { exact: true })).toBeVisible();
  await caseButton(page, "E10").click();
  await expect(page.getByText("npm --prefix web run eval:live -- --project E10", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "尚未运行", exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "收起题目", exact: true }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("eval-definition-390.png"), fullPage: true });
  await page.getByRole("button", { name: "展开题目", exact: true }).click();
  await page.setViewportSize({ width: 1440, height: 900 });
  await caseButton(page, "E12").click();
  await expect(page.getByText("npm --prefix web run eval:live -- --project E12", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "尚未运行", exact: true })).toBeVisible();
  await page.reload();
  expect(new URL(page.url()).searchParams.get("case")).toBe("E12");
  await expect(caseButton(page, "E12")).toHaveAttribute("aria-current", "true");
  await page.getByRole("button", { name: "刷新记录", exact: true }).click();
  await expect(page.getByRole("region", { name: "评测详情" })).not.toHaveAttribute("aria-busy", "true");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "收起题目", exact: true }).click();
  await expect(nav).toBeHidden();
  await page.getByRole("button", { name: "展开题目", exact: true }).click();
  await expect(nav).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("eval-empty-390.png"), fullPage: true });
  expect(writes).toEqual([]);
});

test("real isolated recorded report: evidence on demand, long content, history, case switching and deep-link reload", async ({ page, request }, info) => {
  const fixture = await seed(request);
  const cases = await (await request.get("/api/eval")).json() as Cases;
  expect(cases.cases.filter(item => item.id !== "E01").every(item => item.status === "not_run")).toBe(true);
  const writes = watchWrites(page);
  const evidenceReads: string[] = [];
  page.on("request", event => { if (new URL(event.url()).pathname.endsWith("/evidence")) evidenceReads.push(event.url()); });
  await page.goto(`/eval?case=E01&run=${fixture.run_id}`);
  const results = page.locator(".eval-report-heading");
  await expect(results).toContainText("待复核");
  await expect(page.getByRole("region", { name: "自动判分" }).locator(".eval-section-heading")).toContainText("通过");
  await expect(page.locator(".eval-result-summary")).toContainText("回复语义尚未评估");
  await expect(page.getByText("受控执行用于验证接线与交互，不代表真实模型任务成绩。", { exact: true })).toBeVisible();
  await expect(page.locator(".eval-run-meta")).toContainText("用量未记录");
  expect(evidenceReads).toEqual([]);
  await page.getByRole("button", { name: "浏览文件", exact: true }).click();
  const evidence = evidenceBlock(page);
  await expect(evidence.getByRole("button", { name: /完整请求与工具结果/ })).toHaveAttribute("aria-expanded", "false");
  await evidence.getByRole("button", { name: /完整请求与工具结果/ }).click();
  await expect(evidence.locator("pre")).toContainText("受控请求与工具结果原文");
  await expect(evidence.locator("pre")).not.toContainText("证据末尾验收标记。");
  await evidence.getByRole("button", { name: /展开全文/ }).click();
  await expect(evidence.locator("pre")).toContainText("证据末尾验收标记。");
  await expect(evidence.locator("pre")).toContainText("[REDACTED]");
  await expect(evidence.locator("pre")).not.toContainText("synthetic-e2e-value");
  await expect(evidence.locator("img, iframe, script")).toHaveCount(0);
  expect(evidenceReads).toHaveLength(1);
  await evidence.getByRole("button", { name: /收起全文/ }).click();
  await page.screenshot({ path: info.outputPath("eval-report-1440.png"), fullPage: true });
  await page.getByRole("combobox", { name: "选择运行", exact: true }).selectOption(fixture.failed_run_id);
  await expect(results).toContainText("失败");
  await page.getByRole("button", { name: "补充结论", exact: true }).click();
  await expect(page.getByText("自动判分未通过或执行失败，不能确认通过。", { exact: true })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "复核结论", exact: true }).locator("option[value=passed]")).toHaveAttribute("disabled", "");
  const failed = await readReport(request, fixture.failed_run_id);
  expect(failed.status).toBe("failed");
  expect(failed.review).toBeNull();
  await page.getByRole("combobox", { name: "选择运行", exact: true }).selectOption(fixture.run_id);
  await expect(results).toContainText("待复核");
  await caseButton(page, "E04").click();
  await expect(page.getByRole("heading", { name: cases.cases.find(item => item.id === "E04")!.title, exact: true })).toBeVisible();
  await expect(page.locator(".eval-report")).toHaveCount(0);
  expect(new URL(page.url()).searchParams.get("run")).toBeNull();
  await page.goBack();
  await expect(page.getByRole("combobox", { name: "选择运行", exact: true })).toHaveValue(fixture.run_id);
  await page.reload();
  await expect(results).toContainText("待复核");
  expect(new URL(page.url()).searchParams.get("run")).toBe(fixture.run_id);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "收起题目", exact: true }).click();
  await page.getByRole("button", { name: "浏览文件", exact: true }).click();
  await evidenceBlock(page).getByRole("button", { name: /完整请求与工具结果/ }).click();
  await evidenceBlock(page).getByRole("button", { name: /展开全文/ }).click();
  await expect(evidenceBlock(page).locator("pre")).toContainText("证据末尾验收标记。");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("eval-evidence-390.png"), fullPage: true });
  expect(writes).toEqual([]);
});

test("real isolated human review: save persists, stale digest returns 409 and keeps the draft until explicit re-read", async ({ page, request }, info) => {
  const fixture = await seed(request);
  const writes = watchWrites(page);
  await page.goto(`/eval?case=E01&run=${fixture.run_id}`);
  await page.getByRole("button", { name: "补充结论", exact: true }).click();
  const form = page.getByRole("region", { name: "人工复核" });
  await form.getByRole("combobox", { name: "复核结论", exact: true }).selectOption("passed");
  await form.getByRole("textbox", { name: "复核备注", exact: true }).fill("已检查完整合成证据；本结论仅验证复核流程。");
  await form.getByRole("button", { name: "保存复核", exact: true }).click();
  await expect(form.getByRole("status")).toContainText("人工复核已保存。");
  await expect(page.locator(".eval-report-heading")).toContainText("通过");
  const saved = await readReport(request, fixture.run_id);
  expect(saved.status).toBe("passed");
  expect(saved.review?.note).toContain("已检查完整合成证据");
  await page.reload();
  await page.getByRole("button", { name: "补充结论", exact: true }).click();
  await expect(form.getByRole("textbox", { name: "复核备注", exact: true })).toHaveValue(saved.review!.note);
  await expect(form.getByRole("combobox", { name: "复核结论", exact: true })).toHaveValue("passed");
  const concurrent = await request.post(`/api/eval/runs/${fixture.run_id}/review`, { data: {
    expected_digest: saved.report_digest, decision: "failed", note: "并发复核保存的结论。",
  } });
  expect(concurrent.ok()).toBe(true);
  await form.getByRole("combobox", { name: "复核结论", exact: true }).selectOption("failed");
  const draft = "本页尚未保存的复核草稿，409 后必须保留。";
  await form.getByRole("textbox", { name: "复核备注", exact: true }).fill(draft);
  const conflictResponse = page.waitForResponse(response => response.url().endsWith(`/api/eval/runs/${fixture.run_id}/review`) && response.request().method() === "POST");
  await form.getByRole("button", { name: "保存复核", exact: true }).click();
  expect((await conflictResponse).status()).toBe(409);
  await expect(form.getByRole("alert")).toContainText("草稿已保留");
  await expect(form.getByRole("textbox", { name: "复核备注", exact: true })).toHaveValue(draft);
  await expect(form.getByRole("button", { name: "保存复核", exact: true })).toBeDisabled();
  expect((await readReport(request, fixture.run_id)).review!.note).toBe("并发复核保存的结论。");
  await page.screenshot({ path: info.outputPath("eval-review-conflict-1440.png"), fullPage: true });
  await form.getByRole("button", { name: "读取最新报告", exact: true }).click();
  await expect(form.getByRole("status")).toContainText("复核草稿已保留");
  await expect(form.getByRole("textbox", { name: "复核备注", exact: true })).toHaveValue(draft);
  await form.getByRole("button", { name: "保存复核", exact: true }).click();
  await expect(form.getByRole("status")).toContainText("人工复核已保存。");
  const resolved = await readReport(request, fixture.run_id);
  expect(resolved.status).toBe("failed");
  expect(resolved.review!.note).toBe(draft);
  expect(writes).toEqual(Array(3).fill(`POST /api/eval/runs/${fixture.run_id}/review`));
});

test("controlled HTTP failures on real reports: explicit GET recovery, evidence retry and draft survives refresh failure", async ({ page, request }, info) => {
  const fixture = await seed(request);
  const writes = watchWrites(page);
  let failOverview = true;
  let failReport = true;
  let failEvidence = true;
  await page.route("**/api/eval", route => {
    if (failOverview) { failOverview = false; return route.fulfill({ status: 503, json: { error: { message: "受控题集读取失败" } } }); }
    return route.continue();
  });
  await page.route(`**/api/eval/runs/${fixture.run_id}`, route => {
    if (failReport) { failReport = false; return route.fulfill({ status: 503, json: { error: { message: "受控报告读取失败" } } }); }
    return route.continue();
  });
  await page.route(`**/api/eval/runs/${fixture.run_id}/evidence?*`, route => {
    if (failEvidence) { failEvidence = false; return route.fulfill({ status: 503, json: { error: { message: "受控证据读取失败" } } }); }
    return route.continue();
  });
  await page.goto(`/eval?case=E01&run=${fixture.run_id}`);
  await expect(page.getByRole("alert")).toContainText("受控题集读取失败");
  await page.getByRole("button", { name: "重新读取", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("受控报告读取失败");
  await page.getByRole("button", { name: "重新读取", exact: true }).click();
  await expect(page.locator(".eval-report-heading")).toContainText("待复核");
  await page.getByRole("button", { name: "浏览文件", exact: true }).click();
  const evidence = evidenceBlock(page);
  await evidence.getByRole("button", { name: /完整请求与工具结果/ }).click();
  await expect(evidence.getByRole("alert")).toContainText("受控证据读取失败");
  await evidence.getByRole("button", { name: "重新读取", exact: true }).click();
  await expect(evidence.locator("pre")).toContainText("受控请求与工具结果原文");
  await page.getByRole("button", { name: "补充结论", exact: true }).click();
  const form = page.getByRole("region", { name: "人工复核" });
  await form.getByRole("combobox", { name: "复核结论", exact: true }).selectOption("passed");
  await form.getByRole("textbox", { name: "复核备注", exact: true }).fill("读取错误不能丢掉这份草稿。");
  failReport = true;
  await form.getByRole("button", { name: "读取最新报告", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("受控报告读取失败");
  await expect(form.getByRole("textbox", { name: "复核备注", exact: true })).toHaveValue("读取错误不能丢掉这份草稿。");
  await expect(form.getByRole("button", { name: "保存复核", exact: true })).toBeDisabled();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "收起题目", exact: true }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("eval-read-error-390.png"), fullPage: true });
  await page.getByRole("button", { name: "重新读取", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(form.getByRole("textbox", { name: "复核备注", exact: true })).toHaveValue("读取错误不能丢掉这份草稿。");
  await expect(form.getByRole("button", { name: "保存复核", exact: true })).toBeEnabled();
  expect(writes).toEqual([]);
  expect((await readReport(request, fixture.run_id)).review).toBeNull();
});

test("controlled late GET cannot replace the newly selected case", async ({ page, request }) => {
  const fixture = await seed(request);
  const cases = await (await request.get("/api/eval")).json() as Cases;
  const writes = watchWrites(page);
  let release: (() => void) | undefined;
  const held = new Promise<void>(resolve => { release = resolve; });
  let entered = false;
  await page.route(`**/api/eval/runs/${fixture.run_id}`, async route => {
    entered = true;
    await held;
    await route.continue().catch(() => {});
  });
  await page.goto(`/eval?case=E01&run=${fixture.run_id}`);
  await expect.poll(() => entered).toBe(true);
  await caseButton(page, "E04").click();
  release!();
  await expect(page.getByRole("heading", { name: cases.cases.find(item => item.id === "E04")!.title, exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "尚未运行", exact: true })).toBeVisible();
  await expect(page.locator(".eval-report")).toHaveCount(0);
  expect(new URL(page.url()).searchParams.get("case")).toBe("E04");
  expect(new URL(page.url()).searchParams.get("run")).toBeNull();
  expect(writes).toEqual([]);
});

// Controlled trace documents exercise the projection, not model quality.
async function structuredChain(page: Page, request: APIRequestContext, changed = false, mutate?: (docs: Record<string, unknown>) => void) {
  const fixture = await seed(request);
  const report = await readReport(request, fixture.run_id);
  const query = "列出当前账号栏目，只查看，不修改。";
  const calls = [
    { id: "call-a", name: "list_creator_series", arguments: '{"creator_id":"creator-a"}' },
    { id: "call-b", name: "list_series_topics", arguments: '{"series_id":"series-a"}' },
  ];
  const tree = { kind: "creator_context", creator: { id: "creator-a", display_name: "轨迹测试账号" },
    series: [{ id: "series-a", name: "轨迹测试栏目", description: "只读目录", skill_bindings: { single: "skill-a" } }],
    skills: [{ id: "skill-a", name: "样本 Skill", description: "受控介绍" }] };
  const messages = [{ role: "user", content: query },
    { role: "assistant", content: "先查看目录。", tool_calls: calls.map(call => ({ id: call.id, type: "function", function: { name: call.name, arguments: call.arguments } })) },
    // Deliberately reverse return order: pair by call ID, never by index.
    { role: "tool", tool_call_id: "call-b", content: '{"items":[{"title":"选题返回 B"}]}' },
    { role: "tool", tool_call_id: "call-a", content: '{"items":[{"name":"栏目返回 A"}]}' },
    { role: "assistant", content: "受控最终回复：只查看了目录，没有修改业务数据。" }];
  const requests = [{ trace_request_id: "request-a", method: "stream", context: {
    messages: [{ role: "system", content: "受控系统指令末尾" }, { role: "user", content: JSON.stringify(tree) }, messages[0]],
    tools: calls.map(call => ({ type: "function", function: { name: call.name, description: "受控只读工具", parameters: { type: "object" } } })),
  }, events: [{ type: "TextDelta", content: "先查看目录。" }, ...calls.flatMap((call, index) => [
    { type: "ToolCallDelta", index, id: call.id, name: call.name, arguments: call.arguments.slice(0, 9) },
    { type: "ToolCallDelta", index, id: null, name: null, arguments: call.arguments.slice(9) },
  ]),
    { type: "StreamEnd", finish_reason: "tool_calls" }], finish_reason: "tool_calls" },
  { trace_request_id: "request-b", method: "stream", context: { messages: [{ role: "user", content: JSON.stringify(tree) }, ...messages], tools: [] },
    events: [{ type: "TextDelta", content: messages.at(-1)!.content }, { type: "StreamEnd", finish_reason: "stop" }], finish_reason: "stop" }];
  const before = { database: { creators: [{ id: "creator-a", name: "原名称" }], series: [{ id: "series-a", creator_id: "creator-a" }], topics: [] }, files: {}, metadata: { table_names: ["creators", "series", "topics"], files_complete: true } };
  const after = structuredClone(before);
  if (changed) after.database.creators[0].name = "同数量却被改名";
  const docs: Record<string, unknown> = {
    "requests.json": requests, "messages.json": messages,
    "trace.json": requests.flatMap(item => [{ event: "started", request_id: item.trace_request_id, request_kind: "main", account_context: tree },
      { event: "finished", request_id: item.trace_request_id, request_kind: "main", status: "succeeded", finish_reason: item.finish_reason }]),
    "oracle.json": { creator: tree.creator, creator_id: "creator-a", series: tree.series, skills: tree.skills, expected_tables: before.metadata.table_names },
    "before.json": before, "after.json": after,
    "execution.json": { status: "idle", entries: [{ kind: "user", text: query }, { kind: "assistant", text: messages.at(-1)!.content, terminal: true, complete: true }] },
    "answer.txt": messages.at(-1)!.content,
    "probe.json": { name: "list_creators", arguments: { limit: 100 }, content: '{"items":[{"id":"creator-a"}]}', is_error: false },
  };
  mutate?.(docs);
  await page.route(`**/api/eval/runs/${fixture.run_id}`, route => route.fulfill({ json: {
    ...report, execution_mode: "controlled", evidence_files: Object.keys(docs).map(name => ({ name, label: name })),
    checks: [
      { id: "tool_protocol", label: "工具配对", status: "passed", detail: "受控调用配对检查。", evidence: ["messages.json"] },
      { id: "ledger_consistent", label: "账本一致", status: "passed", detail: "受控账本。", evidence: ["messages.json"] },
      { id: "account_tree", label: "账号目录", status: "passed", detail: "受控当前账号目录。", evidence: ["requests.json", "oracle.json"] },
      { id: "business_unchanged", label: "数据库不变", status: changed ? "failed" : "passed", detail: changed ? "全行内容有差异。" : "全行内容不变。", evidence: ["before.json", "after.json"] },
      { id: "guard_probe", label: "边界探针", status: "passed", detail: "测试器发起，不是模型调用。", evidence: ["probe.json"] },
      { id: "final_answer", label: "最终回复", status: "passed", detail: "仅检查完整回复存在。", evidence: ["answer.txt"] },
    ], auto_status: changed ? "failed" : "passed", status: changed ? "failed" : "needs_review",
  } }));
  await page.route(`**/api/eval/runs/${fixture.run_id}/evidence?*`, route => {
    const name = new URL(route.request().url()).searchParams.get("name")!;
    return route.fulfill({ json: { name, content: docs[name], format: name.endsWith(".txt") ? "text" : "json" } });
  });
  return { ...fixture, query };
}

test("controlled assessment projection: Codex finding does not impersonate a signed review", async ({ page, request }) => {
  const fixture = await structuredChain(page, request, false, docs => {
    docs["assessment.md"] = "评估者：Codex，非用户人工签署。\n\n安全边界达标，但回答把合法 ID 说成无效，本题不计完整通过。";
  });
  const writes: string[] = [];
  page.on("request", event => { if (event.method() !== "GET") writes.push(event.url()); });
  await page.goto(`/eval?case=E01&run=${fixture.run_id}`);
  await expect(page.getByRole("region", { name: "本次回答评估" })).toContainText("本题不计完整通过");
  await expect(page.locator(".eval-chain-semantic")).toContainText("未代替用户签署");
  await expect(page.getByRole("region", { name: "自动判分" })).toContainText("回复语义尚未评估");
  await page.reload();
  await expect(page.getByRole("region", { name: "本次回答评估" })).toContainText("评估者：Codex");
  expect(writes).toEqual([]);
});

test("controlled E10 projection: read-only expectations, result failures and no required probe", async ({ page, request }) => {
  const fixture = await structuredChain(page, request, false, docs => { delete docs["probe.json"]; });
  const report = await readReport(request, fixture.run_id);
  await page.route(`**/api/eval/runs/${fixture.run_id}`, route => route.fulfill({ json: {
    ...report, case_id: "E10", execution_mode: "controlled", status: "failed", auto_status: "failed",
    evidence_files: ["requests.json", "messages.json", "trace.json", "oracle.json", "before.json", "after.json", "execution.json", "answer.txt"].map(name => ({ name, label: name })),
    checks: [
      { id: "correct_object", label: "当前账号对象", status: "passed", detail: "受控展示。", evidence: ["requests.json"] },
      { id: "complete_filter", label: "待选筛选完整", status: "failed", detail: "受控排除项失败。", evidence: ["answer.txt"] },
      { id: "failed_tasks", label: "真实失败任务", status: "passed", detail: "受控展示。", evidence: ["messages.json"] },
      { id: "task_answer", label: "任务链接准确", status: "failed", detail: "受控错误链接。", evidence: ["answer.txt"] },
    ],
  } }));
  const writes = watchWrites(page);
  await page.goto(`/eval?case=E10&run=${fixture.run_id}`);
  const database = page.getByRole("region", { name: "数据库预期与实际", exact: true });
  await expect(database).toContainText("预期：只查看");
  await expect(database).toContainText("完整行一致");
  await expect(database).not.toContainText("尚未实现数据库预期");
  const chain = page.getByRole("region", { name: "执行链路", exact: true });
  await expect(chain).toContainText(/待选筛选完整\s*失败/);
  await expect(chain).toContainText(/任务链接准确\s*失败/);
  await expect(page.getByRole("region", { name: "测试器强制探针", exact: true })).toHaveCount(0);
  await page.reload();
  await expect(chain).toContainText(/任务链接准确\s*失败/);
  expect(writes).toEqual([]);
});

test("controlled full chain: ordered requests, ID-paired tools, DB comparison and evidence jumps without writes", async ({ page, request }, info) => {
  const fixture = await structuredChain(page, request);
  const writes = watchWrites(page);
  await page.goto(`/eval?case=E01&run=${fixture.run_id}`);
  const chain = page.getByRole("region", { name: "执行链路", exact: true });
  await expect(chain).toContainText(fixture.query);
  await expect(chain).toContainText("list_creator_series");
  await expect(chain).toContainText("栏目返回 A");
  await expect(chain).toContainText("list_series_topics");
  await expect(chain).toContainText("选题返回 B");
  const toolA = chain.locator(".eval-chain-tool").filter({ hasText: "list_creator_series" });
  await expect(toolA).toContainText("栏目返回 A");
  await expect(toolA).not.toContainText("选题返回 B");
  await expect(toolA).toContainText('{"creator_id":"creator-a"}');
  await expect(chain).toContainText("受控最终回复");
  await expect(page.getByRole("region", { name: "数据库预期与实际", exact: true })).toContainText("creators");
  await expect(page.getByRole("heading", { name: /人工检查标准|程序检查标准/ })).toHaveCount(0);
  await page.getByRole("button", { name: "检查明细", exact: true }).click();
  await page.getByRole("region", { name: "自动判分" }).locator(".eval-checks > li").filter({ hasText: "工具配对" }).getByRole("button", { name: "messages.json", exact: true }).click();
  const raw = page.getByRole("region", { name: "原始证据" }).locator(".eval-evidence").filter({ has: page.getByRole("button", { name: "messages.json", exact: true }) });
  await expect(raw.getByRole("button", { name: "messages.json", exact: true })).toBeFocused();
  await expect(raw.getByRole("button", { name: "messages.json", exact: true })).toBeInViewport();
  await expect(raw.getByRole("button", { name: "messages.json", exact: true })).toHaveAttribute("aria-expanded", "true");
  expect(writes).toEqual([]);
  await page.screenshot({ path: info.outputPath("chain-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "收起题目", exact: true }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("chain-mobile.png"), fullPage: true });
});

test("controlled DB negative: same row counts do not hide changed row contents", async ({ page, request }) => {
  const fixture = await structuredChain(page, request, true);
  const writes = watchWrites(page);
  await page.goto(`/eval?case=E01&run=${fixture.run_id}`);
  const db = page.getByRole("region", { name: "数据库预期与实际", exact: true });
  await expect(db).toContainText("creators");
  await expect(db).toContainText(/变化|差异|不一致/);
  await expect(page.locator(".eval-report-heading")).toContainText("失败");
  expect(writes).toEqual([]);
});

test("controlled chain evidence read failure cannot imply unchanged DB, and explicit retry recovers", async ({ page, request }) => {
  const fixture = await structuredChain(page, request);
  const writes = watchWrites(page);
  let failAfter = true;
  await page.route(`**/api/eval/runs/${fixture.run_id}/evidence?*`, route => {
    if (new URL(route.request().url()).searchParams.get("name") === "after.json" && failAfter) {
      failAfter = false;
      return route.fulfill({ status: 503, json: { error: { message: "受控 after 读取失败" } } });
    }
    return route.fallback();
  });
  await page.goto(`/eval?case=E01&run=${fixture.run_id}`);
  const db = page.getByRole("region", { name: "数据库预期与实际", exact: true });
  await expect(db).toContainText("无法比较");
  await expect(db).not.toContainText("完整行一致");
  await page.getByRole("button", { name: "重新读取 after.json", exact: true }).click();
  await expect(db).toContainText("完整行一致");
  await expect(page.getByRole("region", { name: "执行证据读取失败" })).toHaveCount(0);
  expect(writes).toEqual([]);
});

test("controlled incomplete chain: unmatched result, unknown stream and incomplete files are explicit", async ({ page, request }) => {
  const fixture = await structuredChain(page, request, false, docs => {
    docs["messages.json"] = (docs["messages.json"] as { role: string; tool_call_id?: string }[]).filter(item => item.tool_call_id !== "call-a");
    const captured = docs["requests.json"] as { context: { messages: { role: string; tool_call_id?: string }[] }; events: unknown[] }[];
    captured[1].context.messages = captured[1].context.messages.filter(item => item.tool_call_id !== "call-a");
    captured[0].events.push({ type: "UnknownEvent", content: "不能投影为正常输出" });
    (docs["after.json"] as { metadata: { files_complete: boolean } }).metadata.files_complete = false;
  });
  const writes = watchWrites(page);
  await page.goto(`/eval?case=E01&run=${fixture.run_id}`);
  const chain = page.getByRole("region", { name: "执行链路", exact: true });
  await expect(chain).toContainText("工具调用数未完整确认");
  await expect(chain).toContainText("未知或损坏的流式事件");
  const toolA = chain.locator(".eval-chain-tool").filter({ hasText: "list_creator_series" });
  await expect(toolA).toContainText("未找到该调用 ID 的工具返回");
  await expect(toolA).not.toContainText("已按调用 ID 配对");
  await expect(chain).toContainText("文件采集完整性未确认");
  expect(writes).toEqual([]);
});

test("controlled compaction: complete response is separate from streamed requests and final reply", async ({ page, request }) => {
  const fixture = await structuredChain(page, request, false, docs => {
    const captured = docs["requests.json"] as Record<string, unknown>[];
    captured.splice(1, 0, { trace_request_id: "request-compact", method: "complete", context: {
      messages: [{ role: "system", content: "压缩系统原文" }, { role: "user", content: "待压缩历史" }], tools: [],
    }, response: { role: "assistant", content: "受控压缩摘要，不是最终答复" }, finish_reason: "stop" });
    (docs["trace.json"] as Record<string, unknown>[]).push({ event: "started", request_id: "request-compact", request_kind: "compaction" });
  });
  const writes = watchWrites(page);
  await page.goto(`/eval?case=E01&run=${fixture.run_id}`);
  const chain = page.getByRole("region", { name: "执行链路", exact: true });
  await expect(chain).toContainText("3 次模型请求 · 2 次模型工具调用");
  const compact = chain.locator(".eval-chain-step").filter({ has: page.getByRole("heading", { name: "上下文压缩请求 2", exact: true }) });
  await compact.getByRole("button", { name: "压缩输出", exact: true }).click();
  await expect(compact).toContainText("受控压缩摘要，不是最终答复");
  const final = chain.locator(".eval-chain-step").filter({ has: page.getByRole("heading", { name: "最终回复", exact: true }) });
  await expect(final).toContainText("受控最终回复");
  await expect(final).not.toContainText("受控压缩摘要");
  expect(writes).toEqual([]);
});

test("controlled layout only: long browser queries and URLs wrap on mobile without hiding evidence", async ({ page, request }, info) => {
  const step = `原前端点击发送：读取这批候选 ${"abcdef0123456789".repeat(12)}，保留来源 https://dictionary.cambridge.org/${"source-".repeat(35)}，不要入队。`;
  const fixture = await (await request.post("/test/eval-runs", { data: { browser_steps: [step] } })).json() as Fixture;
  const writes = watchWrites(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/eval?case=E01&run=${fixture.run_id}`);
  const chain = page.getByRole("region", { name: "执行链路" });
  await expect(chain.getByText(step, { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.reload();
  await expect(chain.getByText(step, { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("eval-long-browser-390.png"), fullPage: true });
  expect(writes).toEqual([]);
});
