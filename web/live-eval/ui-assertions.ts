import { expect, type APIRequestContext, type Locator, type Page } from "@playwright/test";

// Shared only by the explicit live collector and ordinary controlled UI tests.
// These checks verify evidence delivery, not model quality or grader outcomes.
export async function expectRenderedReply(reply: Locator, renderedText: string, timeout = 30_000) {
  await expect(reply).toBeVisible({ timeout });
  await expect.poll(() => reply.innerText(), { timeout,
    message: "Refresh must preserve the same rendered reply, including Markdown tables" }).toBe(renderedText);
}

export interface EvalViewEvidence {
  userQueries: string[];
  requests: Array<{ trace_request_id?: string; request_id?: string; context?: unknown }>;
  before: { database: Record<string, unknown[]> };
  after: { database: Record<string, unknown[]> };
  oracle: { expected_row_counts?: Record<string, number> };
  answer: string;
}

export async function readEvalViewEvidence(request: APIRequestContext, runId: string, userQueries: string[]): Promise<EvalViewEvidence> {
  async function read(name: string) {
    const response = await request.get(`/api/eval/runs/${encodeURIComponent(runId)}/evidence?${new URLSearchParams({ name })}`);
    expect(response.ok(), `${name} must be readable`).toBe(true);
    const file = await response.json();
    expect(file.name).toBe(name);
    return file.content;
  }
  const [requests, before, after, oracle, answer] = await Promise.all(
    ["requests.json", "before.json", "after.json", "oracle.json", "answer.txt"].map(read));
  expect(Array.isArray(requests)).toBe(true);
  expect(requests.length).toBeGreaterThan(0);
  expect(typeof answer).toBe("string");
  expect(answer.trim().length).toBeGreaterThan(0);
  return { userQueries, requests, before, after, oracle, answer };
}

async function fullText(block: Locator, expected: string, expandName: string, timeout: number, description: string) {
  const pre = block.locator("pre").first();
  await expect(pre).toBeVisible({ timeout });
  const expand = block.getByRole("button", { name: new RegExp(`^${expandName}`) });
  if (await expand.count()) await expand.click();
  await expect.poll(() => pre.textContent(), { timeout, message: description }).toBe(expected);
}

export async function expectEvalEvidence(page: Page, evidence: EvalViewEvidence, timeout = 30_000) {
  const chain = page.getByRole("region", { name: "执行链路", exact: true });
  await expect(chain).toBeVisible({ timeout });
  await expect(chain.getByRole("status")).toHaveCount(0, { timeout });
  await expect(chain.getByRole("region", { name: "执行证据读取失败" })).toHaveCount(0);

  const input = chain.locator(".eval-chain-step").filter({ has: page.getByRole("heading", { name: "用户输入", exact: true }) });
  await expect(input.locator(".eval-chain-text pre").first()).toBeVisible({ timeout });
  for (const expand of await input.getByRole("button", { name: /^展开用户输入/ }).all()) await expand.click();
  const actualQueries = await input.locator(".eval-chain-text pre").allTextContents();
  for (const query of evidence.userQueries) expect(actualQueries, "Execution chain must show the actual sent query").toContain(query);

  for (const request of evidence.requests) {
    const id = request.trace_request_id || request.request_id;
    expect(id, "Actual model request needs a recorded identity").toBeTruthy();
    const step = chain.locator(".eval-chain-step").filter({ has: page.getByText(id!, { exact: true }) });
    await expect(step.locator(".eval-chain-step-heading h4")).toHaveText(/^(?:模型请求|上下文压缩请求)/);
    expect(request.context, `Actual context missing for ${id}`).toBeTruthy();
    const context = step.locator(".eval-chain-fold").filter({ has: page.getByRole("button", { name: "请求消息与上下文", exact: true }) });
    await context.getByRole("button", { name: "请求消息与上下文", exact: true }).click();
    await fullText(context, JSON.stringify(request.context, null, 2), "展开上下文", timeout, `Actual request context ${id} must load fully`);
    await context.getByRole("button", { name: "请求消息与上下文", exact: true }).click();
  }

  const database = chain.getByRole("region", { name: "数据库预期与实际", exact: true });
  await expect(database.locator("tbody tr").first()).toBeVisible({ timeout });
  const otherTables = database.getByRole("button", { name: /^其他 \d+ 张表：完整行一致$/ });
  if (await otherTables.count()) await otherTables.click();
  const readonly = ["E01", "E02", "E03", "E04", "E05", "E10"].includes(new URL(page.url()).searchParams.get("case") ?? "");
  const names = Object.keys(evidence.after.database);
  expect(names.length, "Actual database snapshot must contain business tables").toBeGreaterThan(0);
  for (const name of names) {
    expect(Array.isArray(evidence.before.database[name])).toBe(true);
    expect(Array.isArray(evidence.after.database[name])).toBe(true);
    const row = database.locator("tbody tr").filter({ has: page.getByText(name, { exact: true }) });
    const before = readonly && typeof evidence.oracle.expected_row_counts?.[name] === "number"
      ? evidence.oracle.expected_row_counts[name] : evidence.before.database[name].length;
    await expect.poll(() => row.getByRole("cell").nth(0).evaluate(el => el.firstChild?.textContent),
      { timeout, message: `Expected/baseline row count for ${name}` }).toBe(String(before));
    await expect(row.getByRole("cell").nth(1)).toHaveText(String(evidence.after.database[name].length));
  }

  const original = page.getByRole("region", { name: "原始证据", exact: true });
  const opened: string[] = [];
  for (const [name, value, source] of [
    ["before.json", evidence.before, database], ["after.json", evidence.after, database],
    ["answer.txt", evidence.answer, chain.locator(".eval-chain-step").filter({ has: page.getByRole("heading", { name: "最终回复", exact: true }) })],
  ] as const) {
    await source.getByRole("button", { name, exact: true }).click();
    const file = original.locator(".eval-evidence").filter({ has: page.locator("button.eval-reveal").filter({ hasText: name }) });
    await expect(file.locator("button.eval-reveal")).toHaveAttribute("aria-expanded", "true");
    await fullText(file, typeof value === "string" ? value : JSON.stringify(value, null, 2), "展开全文", timeout, `${name} must load the complete recorded body`);
    opened.push(name);
    await file.locator("button.eval-reveal").click();
  }
  await original.getByRole("button", { name: "收起文件", exact: true }).click();
  return { user_queries_verified: evidence.userQueries.length, model_requests_verified: evidence.requests.length,
    database_tables_verified: names, evidence_files_opened: opened };
}

export async function expectProductionRecord(page: Page, run: { id: string; status: string; error_message?: string | null; publication?: unknown }) {
  const record = page.locator(".run-workbench");
  const labels: Record<string, string> = { queued: "待开始", producing: "生产中", validating: "验收中", awaiting_approval: "待批准",
    interrupted: "已中断", failed: "失败", approved: "已批准", cancelled: "已取消" };
  if (run.publication) await expect(record.locator(".run-title-row .status-active")).toHaveText("已发布");
  else {
    const status = record.locator(".run-title-row .status-pill");
    await expect(status).toHaveClass(new RegExp(`(?:^|\\s)status-${run.status}(?:\\s|$)`));
    await expect(status).toHaveText(labels[run.status] ?? run.status);
  }
  if (run.error_message) await expect(record.locator(".review-warning[role=alert]").first()).toHaveText(run.error_message);
  await record.getByRole("button", { name: /^生产记录/ }).click();
  await expect(record.locator("#run-history").getByText(run.id, { exact: true })).toBeVisible();
  await expect(record.locator("#run-history").getByRole("heading", { name: "状态时间线", exact: true })).toBeVisible();
  await record.getByRole("button", { name: /^收起生产记录/ }).click();
}
