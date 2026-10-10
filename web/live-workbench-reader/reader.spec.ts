import { expect, test } from "@playwright/test";
import { readWorkbenchReport } from "../live-workbench/read-report";

test("unpaid original Eval opens full bytes and real DB rows without a rerun", async ({ page, request }) => {
  const scenario = await (await request.get("/__live_eval__/scenario")).json();
  let mutations = 0;
  page.on("request", row => { if (["POST", "PUT", "PATCH", "DELETE"].includes(row.method()) && new URL(row.url()).pathname.startsWith("/api/")) mutations++; });
  const view = await readWorkbenchReport(page, request, scenario.case_id, scenario.run_id);
  expect(view.evidence_loaded).toBe(true);
  const report = await (await request.get("/__live_eval__/result")).json();
  expect(report.auto_status).toBe("failed");
  expect(report.model.name).toBe("unavailable");
  expect(mutations).toBe(0);
});
