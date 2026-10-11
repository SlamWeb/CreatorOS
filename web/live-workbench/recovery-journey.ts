import { expect, type APIRequestContext, type Page, type TestInfo } from "@playwright/test";
import { readProgress } from "./read-progress";

export async function recoveryJourney(page: Page, request: APIRequestContext, info: TestInfo,
  scenario: { creator_name: string; series_name: string; query: string }, browser: Record<string, unknown>) {
  const steps = browser.steps as string[];
  await page.goto("/");
  await page.getByRole("button", { name: `查看账号 ${scenario.creator_name}`, exact: true }).click();
  await page.getByRole("button", { name: scenario.series_name, exact: true }).click();
  await expect(page.getByRole("heading", { name: scenario.series_name, level: 1 })).toBeVisible();
  await page.getByLabel("新选题标题", { exact: true }).fill(scenario.query);
  const queued = page.waitForResponse(r => r.request().method() === "POST" && new URL(r.url()).pathname.endsWith("/queue"));
  await page.getByRole("button", { name: "添加", exact: true }).click();
  expect((await queued).status()).toBe(201);
  const card = page.locator(".content-card").filter({ has: page.getByRole("heading", { name: scenario.query, exact: true }) });
  await expect(card).toBeVisible();
  const started = page.waitForResponse(r => r.request().method() === "POST" && new URL(r.url()).pathname.endsWith("/execute"));
  await card.getByRole("button", { name: "生产", exact: true }).click();
  expect((await started).status()).toBe(202);
  await page.getByRole("link", { name: "查看本次运行" }).click();
  const id = new URL(page.url()).pathname.split("/")[2];
  browser.business_run_id = id;
  steps.push("原栏目手动添加选题并点击生产（本题不经过DeepSeek聊天）");
  let status = "";
  await expect.poll(async () => {
    status = (await readProgress(request, `/api/runs/${id}`, browser)).status;
    return status;
  }, { timeout: 1_860_000, intervals: [1000, 3000, 5000] }).toMatch(/^(awaiting_approval|failed|interrupted|cancelled|unknown)$/);
  expect(status, "本题应在真实完成后触发一次声明后处理失败").toBe("failed");
  await expect(page.locator(".run-workbench .run-title-row .status-pill")).toContainText("失败");
  await expect(page.getByRole("button", { name: "恢复生产", exact: true })).toBeEnabled();
  browser.failed_visible = true;
  browser.failed_ui = await page.locator("main").innerText();
  await page.screenshot({ path: info.outputPath("declared-host-failure.png"), fullPage: false });
  expect((await request.post("/__live_eval__/recovery-stage", { data: { name: "failed", run_id: id } })).ok()).toBe(true);
  steps.push("真实SDK及新图完成后，宿主声明后处理失败；采集原失败Attempt/文件/SDK记录");
  const resumed = page.waitForResponse(r => r.request().method() === "POST" && new URL(r.url()).pathname === `/api/runs/${id}/execute`);
  await page.getByRole("button", { name: "恢复生产", exact: true }).click();
  const response = await resumed;
  expect(response.status()).toBe(202);
  browser.resume_clicked = true;
  browser.resume_request = { path: new URL(response.url()).pathname, status: response.status(), body: response.request().postDataJSON() };
  await expect.poll(async () => (await readProgress(request, `/api/runs/${id}`, browser)).status,
    { timeout: 90_000, intervals: [500, 1000, 2000] }).toMatch(/^(awaiting_approval|failed|interrupted|cancelled|unknown)$/);
  const final = await readProgress(request, `/api/runs/${id}`, browser);
  expect(final.status).toBe("awaiting_approval");
  expect((await request.post("/__live_eval__/recovery-stage", { data: { name: "restored", run_id: id } })).ok()).toBe(true);
  steps.push("原Run显式点击恢复，离线新Attempt回到待批准；未批准/发布");
  return id;
}
