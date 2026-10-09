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
const evidenceBlock = (page: Page) => page.getByRole("region", { name: "原始证据" }).locator(".eval-evidence").filter({ has: page.locator("summary").filter({ hasText: "完整请求与工具结果" }) });

test("real isolated empty eval: twelve not-run cases, four categories and refresh never execute", async ({ page, request }, info) => {
  const result = await request.get("/api/eval");
  expect(result.ok()).toBe(true);
  const dataset = await result.json() as Cases;
  expect(dataset.cases).toHaveLength(12);
  expect(dataset.cases.every(item => item.status === "not_run" && item.run_count === 0)).toBe(true);
  const writes = watchWrites(page);
  await page.goto("/");
  await page.getByRole("navigation", { name: "主导航" }).getByRole("link", { name: "Eval", exact: true }).click();
  await expect(page.getByRole("heading", { name: "账号 Agent Eval", exact: true })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "主导航" }).getByRole("link", { name: "Eval", exact: true })).toHaveClass(/active/);
  const nav = page.getByRole("navigation", { name: "题目分类" });
  await expect(nav.getByRole("button")).toHaveCount(12);
  for (const category of ["账号边界", "会话持久化", "状态更新", "工具正确性"]) await expect(nav.getByRole("heading", { name: category, exact: true })).toBeVisible();
  await expect(nav.locator(".eval-status.not_run")).toHaveCount(12);
  await expect(nav.locator(".eval-status.passed")).toHaveCount(0);
  await expect(page.getByText("python -m creatoros.evaluation.run --case E01", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /启动|开始执行|运行评测/ })).toHaveCount(0);
  const definition = page.locator(".eval-case-definition");
  const firstCase = dataset.cases.find(item => item.id === "E01")!;
  await expect(definition).not.toHaveAttribute("open", "");
  await definition.locator("summary").click();
  await expect(definition.getByText(firstCase.definition.steps.find(step => step.kind === "user")!.text!, { exact: true })).toBeVisible();
  await expect(definition.getByText(firstCase.definition.manual_checks[0], { exact: true })).toBeVisible();
  await expect(definition.getByRole("heading", { name: "程序检查标准", exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath("eval-definition-1440.png"), fullPage: true });
  await definition.locator("summary").click();
  await page.screenshot({ path: info.outputPath("eval-empty-1440.png"), fullPage: true });
  await caseButton(page, "E02").click();
  await expect(definition).not.toHaveAttribute("open", "");
  await definition.locator("summary").click();
  const secondCase = dataset.cases.find(item => item.id === "E02")!;
  await expect(definition.getByText(secondCase.definition.steps.find(step => step.kind === "user")!.text!, { exact: true })).toBeVisible();
  await expect(page.getByText("执行器尚未接线。当前可查看题目定义，不能运行此题。", { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "收起题目", exact: true }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("eval-definition-390.png"), fullPage: true });
  await page.getByRole("button", { name: "展开题目", exact: true }).click();
  await page.setViewportSize({ width: 1440, height: 900 });
  await caseButton(page, "E12").click();
  await expect(page.getByText("执行器尚未接线。当前可查看题目定义，不能运行此题。", { exact: true })).toBeVisible();
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
  await expect(page.getByText("自动检查通过，仍需人工核对后才能计为通过。", { exact: true })).toBeVisible();
  await expect(page.getByText("受控执行用于验证接线与交互，不代表真实模型任务成绩。", { exact: true })).toBeVisible();
  await expect(page.locator(".eval-run-meta")).toContainText("用量未记录");
  expect(evidenceReads).toEqual([]);
  const evidence = evidenceBlock(page);
  await expect(evidence).not.toHaveAttribute("open", "");
  await evidence.locator("summary").click();
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
  await evidenceBlock(page).locator("summary").click();
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
  const evidence = evidenceBlock(page);
  await evidence.locator("summary").click();
  await expect(evidence.getByRole("alert")).toContainText("受控证据读取失败");
  await evidence.getByRole("button", { name: "重新读取", exact: true }).click();
  await expect(evidence.locator("pre")).toContainText("受控请求与工具结果原文");
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
