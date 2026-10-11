import { createHash } from "node:crypto";
import { expect, test } from "@playwright/test";

const selected = process.env.CREATOROS_WORKBENCH_READ_CASE;

async function visibleImage(page: import("@playwright/test").Page, request: import("@playwright/test").APIRequestContext) {
  const image = page.getByRole("region", { name: "产物图片" }).locator(".carousel-stage img");
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate((element: HTMLImageElement) => element.complete && element.naturalWidth > 0)).toBe(true);
  const src = await image.evaluate((element: HTMLImageElement) => element.currentSrc);
  const response = await request.get(src);
  expect(response.ok()).toBe(true);
  return createHash("sha256").update(await response.body()).digest("hex");
}

test("unpaid P04 original Run UI saves a queued revision without starting execution", async ({ page, request }, info) => {
  test.skip(selected !== "P04" || !!process.env.CREATOROS_WORKBENCH_READ_RUN_ID, "仅P04隔离SQLite返工保存预检；不执行或评分");
  const scenario = await (await request.get("/__live_eval__/scenario")).json();
  expect(scenario.case_id).toBe("P04");
  const runId = scenario.business_run_id;
  expect(runId).toBeTruthy();
  const before = await (await request.get(`/api/runs/${runId}`)).json();
  const original = before.revisions.find((row: { id: string }) => row.id === scenario.revision_id);
  expect(original).toBeTruthy();
  const originalSnapshot = structuredClone(original);
  await page.goto(`/runs/${runId}`);
  const originalSha = await visibleImage(page, request);
  expect(originalSha).toBe(scenario.image_sha256);
  await page.screenshot({ path: info.outputPath("p04-before-rework.png"), fullPage: true });

  const mutations: Array<{ path: string; method: string; body?: string; status?: number }> = [];
  const byRequest = new WeakMap<object, (typeof mutations)[number]>();
  page.on("request", item => {
    if (!["POST", "PUT", "PATCH", "DELETE"].includes(item.method()) || !new URL(item.url()).pathname.startsWith("/api/")) return;
    const record = { method: item.method(), path: new URL(item.url()).pathname, body: item.postData() ?? undefined };
    mutations.push(record); byRequest.set(item, record);
  });
  page.on("response", response => { const record = byRequest.get(response.request()); if (record) record.status = response.status(); });
  await page.getByRole("button", { name: "提出返工", exact: true }).click();
  await page.getByRole("textbox", { name: "告诉生产者要改哪里", exact: true }).fill(scenario.rework_instruction);
  const saved = page.waitForResponse(response => response.request().method() === "POST"
    && new URL(response.url()).pathname === `/api/runs/${runId}/revisions`);
  await page.getByRole("button", { name: "保存返工要求", exact: true }).click();
  const response = await saved;
  expect(response.status()).toBe(201);
  const after = await response.json();
  const queued = after.revisions.find((row: { revision_number: number }) => row.revision_number === original.revision_number + 1);
  expect(after.revisions).toHaveLength(before.revisions.length + 1);
  expect(after.status).toBe("queued");
  expect(queued.instruction).toBe(scenario.rework_instruction);
  expect(queued.attempts).toHaveLength(0);
  expect(after.revisions.find((row: { id: string }) => row.id === original.id)).toEqual(originalSnapshot);
  const revisionSelect = page.getByRole("combobox", { name: "内容版本", exact: true });
  await expect(revisionSelect.locator("option")).toHaveCount(after.revisions.length);
  await revisionSelect.selectOption(original.id);
  expect(await visibleImage(page, request)).toBe(originalSha);
  await revisionSelect.selectOption(queued.id);
  const imageRegion = page.getByRole("region", { name: "产物图片" });
  await expect(imageRegion.locator(".carousel-stage img")).toHaveCount(0);
  await expect(imageRegion).toContainText("还没有图片");
  await expect(page.getByRole("button", { name: "开始生产", exact: true })).toBeEnabled();
  await page.reload();
  await expect(revisionSelect).toHaveValue(queued.id);
  await expect(imageRegion.locator(".carousel-stage img")).toHaveCount(0);
  expect(mutations.map(row => row.path)).toEqual([`/api/runs/${runId}/revisions`]);
  const counts = await (await request.get(`/api/eval/runs/${scenario.run_id}/evidence?name=execution_counts.json`)).json();
  expect(counts.content.producer_instances).toBe(0);
  await page.screenshot({ path: info.outputPath("p04-saved-queued-no-attempt.png"), fullPage: true });
});

test("unpaid P06 locates the original account chat and selected Run discussion without sending a query", async ({ page, request }, info) => {
  test.skip(selected !== "P06" || !!process.env.CREATOROS_WORKBENCH_READ_RUN_ID, "仅P06隔离只读GUI定位预检；不发送DeepSeek/讨论请求");
  const scenario = await (await request.get("/__live_eval__/scenario")).json();
  expect(scenario.case_id).toBe("P06");
  const runId = scenario.business_run_id;
  const run = await (await request.get(`/api/runs/${runId}`)).json();
  const mutations: string[] = [];
  page.on("request", item => {
    if (["POST", "PUT", "PATCH", "DELETE"].includes(item.method()) && new URL(item.url()).pathname.startsWith("/api/")) {
      mutations.push(`${item.method()} ${new URL(item.url()).pathname}`);
    }
  });
  await page.goto(`/runs/${runId}?revision=${encodeURIComponent(scenario.revision_id)}`);
  await expect(page.getByRole("heading", { name: run.topic_title, exact: true })).toBeVisible();
  const imageSha = await visibleImage(page, request);
  expect(imageSha).toBe(scenario.image_sha256);
  await page.getByRole("button", { name: "讨论", exact: true }).click();
  await expect(page.getByRole("region", { name: /第 1 版的 Codex 讨论/ })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "想和 Codex 核对什么？" })).toBeVisible();
  await expect(page.getByRole("button", { name: "发送讨论", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "生产记录" }).click();
  const runRecords = page.locator(".run-workbench .run-records");
  await expect(runRecords.getByRole("link", { name: "Observation" })).toBeVisible();
  await expect(runRecords.getByText("Thread", { exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath("p06-run-image-discussion-controls.png"), fullPage: true });

  await page.goto("/");
  await page.getByRole("button", { name: `查看账号 ${scenario.creator_name}` }).click();
  await page.getByRole("button", { name: "打开账号对话" }).click();
  const panel = page.locator(".account-chat-panel");
  await expect(panel.getByRole("textbox", { name: "给 Agent 的消息" })).toBeVisible();
  await expect(panel.getByRole("button", { name: "发送 ↑" })).toBeDisabled();
  await expect(panel.locator(".chat-answer")).toHaveCount(0);
  await page.screenshot({ path: info.outputPath("p06-account-chat-empty-composer.png"), fullPage: true });
  expect(mutations).toEqual([]);
});
