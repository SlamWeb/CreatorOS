import { expect, test } from "@playwright/test";
import { readWorkbenchReport } from "../live-workbench/read-report";
import { writeFile } from "node:fs/promises";

test("unpaid original Eval opens full bytes and real DB rows without a rerun", async ({ page, request }, info) => {
  test.skip(process.env.CREATOROS_WORKBENCH_READ_CASE === "P05" && !process.env.CREATOROS_WORKBENCH_READ_RUN_ID, "另有P05免费入队定位预检");
  const existingRun = process.env.CREATOROS_WORKBENCH_READ_RUN_ID;
  const reportUrl = existingRun ? `/api/eval/runs/${existingRun}` : "/__live_eval__/result";
  const beforeReport = await (await request.get(reportUrl)).text();
  const report = JSON.parse(beforeReport);
  const scenario = existingRun ? { run_id: existingRun, case_id: report.case_id }
    : await (await request.get("/__live_eval__/scenario")).json();
  let query: string | undefined;
  if (existingRun) {
    const response = await request.get(`/api/eval/runs/${existingRun}/evidence?name=messages.json`);
    expect(response.ok()).toBe(true);
    const file = await response.json();
    query = file.content.find((row: { role: string }) => row.role === "user")?.content;
  }
  let mutations = 0;
  page.on("request", row => { if (["POST", "PUT", "PATCH", "DELETE"].includes(row.method()) && new URL(row.url()).pathname.startsWith("/api/")) mutations++; });
  const view = await readWorkbenchReport(page, request, scenario.case_id, scenario.run_id, query);
  expect(view.evidence_loaded).toBe(true);
  expect(await (await request.get(reportUrl)).text()).toBe(beforeReport);
  expect(report.auto_status).toBe("failed");
  if (!existingRun) expect(report.model.name).toBe("unavailable");
  expect(mutations).toBe(0);
  await writeFile(info.outputPath("read-only-view-proof.json"), JSON.stringify({
    ...view, run_id: scenario.run_id, business_mutations: mutations, original_report_unchanged: true,
    original_auto_status: report.auto_status, read_only_recheck_not_a_paid_model_repeat: true,
  }, null, 2));
  // Expanded SDK/context evidence can be hundreds of thousands of pixels tall.
  // Full bytes are asserted above; raster proof deliberately uses one viewport.
  await page.goto(`/eval?case=${scenario.case_id}&run=${scenario.run_id}`);
  await expect(page.getByRole("heading", { name: "运行结果", exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath("read-only-eval.png") });
});
