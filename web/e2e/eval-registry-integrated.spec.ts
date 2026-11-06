import { expect, test } from "@playwright/test";
import { spawnSync } from "node:child_process";
import path from "node:path";

test("renders real registry records through the actual read API", async ({ page }) => {
  const root = process.env.CREATOROS_INTERNAL_E2E_ROOT;
  if (!root) throw new Error("The isolated browser root is required");
  const script = `
import sys
from creatoros.evaluation.registry import EvalRegistry
r=EvalRegistry(sys.argv[1])
r.register_round({'id':'integration-round','title':'隔离登记链路','code_version':'test-sha','merge_status':'developing'})
r.register_run({'id':'integration-run','round_id':'integration-round','suite_id':'tool-suite','suite_version':'v1','planned_cases':['B06']})
r.transition('integration-run','freezing')
r.transition('integration-run','running')
r.add_case_result('integration-run',{'case_id':'B06','status':'needs_review','summary':'隔离测试证据','evidence':{'user_requests':['测试请求'], 'program_status':'passed'}})
r.transition('integration-run','completed')
`;
  const seeded = spawnSync(process.env.CREATOROS_PYTHON ?? "python", ["-c", script, path.join(root, "eval-registry.sqlite3")],
    { cwd: path.resolve(import.meta.dirname, "../.."), encoding: "utf8" });
  expect(seeded.status, seeded.stderr).toBe(0);
  await page.goto("/eval");
  await expect(page.getByRole("heading", { name: "隔离登记链路" })).toBeVisible();
  await expect(page.getByRole("region", { name: "测试结果" })).toContainText("1 待复核");
  await page.getByRole("button", { name: /tool-suite/ }).click();
  await page.getByRole("button", { name: /B06/ }).click();
  await expect(page.getByRole("region", { name: "运行详情" })).toContainText("隔离测试证据");
  await expect(page.getByRole("region", { name: "运行详情" })).toContainText("测试请求");
  await page.reload();
  await expect(page.getByRole("region", { name: "运行详情" })).toContainText("隔离测试证据");
});
