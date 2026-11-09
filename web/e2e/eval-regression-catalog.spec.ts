import { expect, test } from "@playwright/test";
import { spawnSync } from "node:child_process";
import path from "node:path";

const member = (id: string, ready: boolean) => ({ instance_id: `base:${id}`, dataset_id: "base-v1", case_id: id, title: `题目 ${id}`, family: "example", parent_instance_id: null, dependencies: ["DeepSeek"], side_effects: ["模型调用"], resource_class: "routine", execution_readiness: ready ? "enabled" : "requires_review", reason: ready ? "已有适配器" : "需要核查副作用" });
const catalog = { schema_version: "regression-catalog-v1", suites: [
  { id: "routine", version: "v1", title: "常规全量回归", description: "受限资源", cases: [member("B06", true)], candidates: [member("B01", false)] },
  { id: "full", version: "v1", title: "完整回归", description: "全部已定义请求", cases: [member("B06", true), member("B01", false)], candidates: [] },
] };

test("browses versioned suites without treating eligibility as results", async ({ page }, info) => {
  await page.route("**/api/eval/v1/rounds", route => route.fulfill({ json: { items: [] } }));
  await page.route("**/api/eval/v1/suites", route => route.fulfill({ json: catalog }));
  await page.route("**/api/eval/v1/suites/*/runs", route => route.fulfill({ json: { items: [] } }));
  await page.goto("/eval");
  await page.getByRole("button", { name: "回归集", exact: true }).click();
  await expect(page.getByRole("heading", { name: "回归集" })).toBeVisible();
  await page.getByRole("button", { name: /常规全量回归/ }).click();
  const scope = page.getByRole("region", { name: "套件范围" });
  await expect(scope).toContainText("已定义");
  await expect(scope).toContainText("1");
  await expect(page.getByRole("region", { name: "已登记运行" })).toContainText("暂无已登记运行");
  await page.getByText("候选题目（不计入正式成员） · 1").click();
  await expect(page.getByRole("region", { name: "候选题目（不计入正式成员）" })).toContainText("题目 B01");
  await page.getByText("题目 B01").click();
  await expect(page.getByRole("region", { name: "候选题目（不计入正式成员）" })).toContainText("需要核查副作用");
  await page.getByLabel("查找题目").fill("B01");
  await expect(page.getByText("没有匹配的正式成员。")).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "常规全量回归" })).toBeVisible();
  await expect(page.getByText("运行结果请在评测工作台查看。", { exact: false })).toBeVisible();
  await page.screenshot({ path: info.outputPath("regression-suite-1440.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("regression-suite-390.png"), fullPage: true });
  await page.getByRole("button", { name: "返回回归集" }).click();
  await expect(page.getByRole("button", { name: /完整回归/ })).toBeVisible();
  await page.getByRole("button", { name: "返回评测工作台" }).click();
  await expect(page.getByText("还没有正式评测记录")).toBeVisible();
});

test("navigates only registered suite runs and preserves their frozen selection", async ({ page }) => {
  const r = {id: "R1", title: "真实登记轮次", change_summary: "", code_version: "sha", branch: "dev", merge_status: "developing", parent_round_id: null, created_at: "2026-11-07T00:00:00Z", changes: []};
  const run = {id: "RUN1", round_id: "R1", suite_id: "adapter", suite_version: "v1", status: "completed", display_status: "completed", planned_cases: ["B06"], created_at: "2026-11-07T00:00:00Z", started_at: null, finished_at: null, metadata: {catalog_binding: {catalog_suite_id: "routine", catalog_suite_version: "v1", selection_kind: "partial", planned_instance_ids: ["base:B06", "base:B01"], selected_instance_ids: ["base:B06"]}}, results: []};
  await page.route("**/api/eval/v1/suites", route => route.fulfill({json: catalog}));
  await page.route("**/api/eval/v1/suites/routine/runs", route => route.fulfill({json: {items: [{round_id:"R1",round_title:r.title,run_id:"RUN1",suite_version:"v1",selection_kind:"partial",status:"completed",display_status:"completed",created_at:r.created_at,selected_count:1,planned_count:2,membership_sha256:"a"}]}}));
  await page.route("**/api/eval/v1/rounds", route => route.fulfill({json: {items: [r]}}));
  await page.route("**/api/eval/v1/rounds/R1", route => route.fulfill({json: {round: r, runs: [run]}}));
  await page.goto("/eval?view=suites&suite=routine");
  const records = page.getByRole("region", {name: "已登记运行"});
  await expect(records).toContainText("范围不完整 · 1 / 2 题");
  await records.getByRole("button", {name: /真实登记轮次/}).click();
  const binding = page.getByRole("region", {name: "所属回归集"});
  await expect(binding).toContainText("常规全量回归");
  await expect(binding).toContainText("范围不完整 · 1 / 2 题");
  await page.reload();
  await expect(page.getByRole("region", {name: "所属回归集"})).toContainText("范围不完整 · 1 / 2 题");
  await binding.getByRole("button", {name: "查看题集"}).click();
  await expect(page.getByRole("heading", {name: "常规全量回归"})).toBeVisible();
});

test("surfaces directory failures and can retry", async ({ page }) => {
  await page.route("**/api/eval/v1/suites", route => route.fulfill({ status: 503, json: { error: { message: "目录暂不可用" } } }));
  await page.goto("/eval?view=suites");
  await expect(page.getByRole("alert")).toContainText("目录暂不可用");
  await page.unroute("**/api/eval/v1/suites");
  await page.route("**/api/eval/v1/suites", route => route.fulfill({ json: catalog }));
  await page.getByRole("button", { name: "重试" }).click();
  await expect(page.getByRole("button", { name: /常规全量回归/ })).toBeVisible();
});

test("keeps current suites accessible when the historical catalog fails", async ({ page }) => {
  await page.route("**/api/eval/v1/suites", route => route.fulfill({json: {
    ...catalog, historical_error: "历史目录暂不可用；历史轮次及运行记录仍可查看。",
  }}));
  await page.route("**/api/eval/v1/suites/*/runs", route => route.fulfill({json: {items: []}}));
  await page.goto("/eval?view=suites");
  await expect(page.getByRole("status")).toContainText("历史目录暂不可用");
  await page.getByRole("button", {name: /常规全量回归/}).click();
  await expect(page.getByRole("heading", {name: "常规全量回归"})).toBeVisible();
  await page.goto("/eval?view=suites&suite=regular-v1");
  await expect(page.getByRole("alert")).toContainText("无法确认该回归集");
});

test("reads the actual versioned catalog from the isolated backend", async ({ page }) => {
  const response = page.waitForResponse(result => result.url().includes("/api/eval/v1/suites") && result.request().method() === "GET");
  await page.goto("/eval?view=suites&suite=full-v9");
  const reply = await response;
  expect(reply.status()).toBe(200);
  const body = await reply.json() as { suites: { id: string; title: string; counts: { defined: number; requires_review: number } }[] };
  const full = body.suites.find(item => item.id === "full-v9");
  const regular = body.suites.find(item => item.id === "regular-v9");
  expect(full).toBeTruthy();
  expect(regular).toBeTruthy();
  expect(full!.counts.defined).toBeGreaterThan(0);
  await expect(page.getByRole("heading", { name: "完整回归" })).toBeVisible();
  const scope = page.getByRole("region", { name: "套件范围" });
  await expect(scope.locator("strong").nth(0)).toHaveText(String(full!.counts.defined));
  await expect(scope.locator("strong").nth(2)).toHaveText(String(full!.counts.requires_review));
  await expect(page.getByText("执行范围尚不完整，不能视为全量运行能力。")).toBeVisible();
  await page.getByRole("button", { name: "返回回归集" }).click();
  await expect(page.getByRole("button", { name: /常规全量回归.*版本 9.*当前版本/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /常规全量回归.*版本 8.*历史版本/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /常规全量回归.*版本 7.*历史版本/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /常规全量回归.*版本 6.*历史版本/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /常规全量回归.*版本 5.*历史版本/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /常规全量回归.*版本 4.*历史版本/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /常规全量回归.*版本 3.*历史版本/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /常规全量回归.*版本 2.*历史版本/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /常规全量回归.*版本 1.*历史版本/ })).toBeVisible();
});

test("opens a real registered suite run through the isolated backend", async ({ page }) => {
  const root = process.env.CREATOROS_INTERNAL_E2E_ROOT;
  if (!root) throw new Error("The isolated browser root is required");
  const script = `
import sys
from creatoros.evaluation.registry import EvalRegistry
from creatoros.evaluation.regression_runner import prepare
r=EvalRegistry(sys.argv[1]); b=prepare('regular-v9', ['base:B06'])['catalog_binding']
r.register_round({'id':'catalog-round','title':'隔离回归轮次','code_version':'test-sha'})
r.register_round({'id':'historical-round','title':'历史回归轮次','code_version':'old-sha'})
r.register_round({'id':'v3-round','title':'第三版回归轮次','code_version':'v3-sha'})
r.register_run({'id':'catalog-run','round_id':'catalog-round','suite_id':'tool-suite','suite_version':'v1','planned_cases':['B06'],'metadata':{'catalog_binding':b}})
r.register_run({'id':'historical-run','round_id':'historical-round','suite_id':'tool-suite','suite_version':'v1','planned_cases':['B06'],'metadata':{'catalog_binding':{**b,'catalog_suite_id':'regular-v1','catalog_suite_version':'1'}}})
r.register_run({'id':'v3-run','round_id':'v3-round','suite_id':'tool-suite','suite_version':'v1','planned_cases':['B06'],'metadata':{'catalog_binding':{**b,'catalog_suite_id':'regular-v3','catalog_suite_version':'3'}}})
r.transition('catalog-run','freezing'); r.transition('catalog-run','running')
r.add_case_result('catalog-run',{'case_id':'B06','status':'needs_review','summary':'隔离证据'})
r.transition('catalog-run','completed')
`;
  const seeded = spawnSync(process.env.CREATOROS_PYTHON ?? "python", ["-c", script, path.join(root, "eval-registry.sqlite3")],
    { cwd: path.resolve(import.meta.dirname, "../.."), encoding: "utf8" });
  expect(seeded.status, seeded.stderr).toBe(0);
  await page.goto("/eval?view=suites&suite=regular-v9");
  const records = page.getByRole("region", {name: "已登记运行"});
  await expect(records).toContainText("隔离回归轮次", { timeout: 20000 });
  await expect(records).not.toContainText("历史回归轮次");
  await expect(records.getByRole("button")).toHaveCount(1);
  await expect(records).toContainText("范围不完整");
  await page.goto("/eval?view=suites&suite=regular-v3");
  await expect(records).toContainText("第三版回归轮次", { timeout: 20000 });
  await expect(records).not.toContainText("隔离回归轮次");
  await expect(records.getByRole("button")).toHaveCount(1);
  await page.goto("/eval?view=suites&suite=regular-v1");
  await expect(page.getByText("历史目录仅供查看，运行记录与当前版本分开保存。")).toBeVisible({ timeout: 20000 });
  await expect(records).toContainText("历史回归轮次");
  await expect(records).not.toContainText("隔离回归轮次");
  await expect(records.getByRole("button")).toHaveCount(1);
  await page.goto("/eval?view=suites&suite=regular-v9");
  await records.getByRole("button", {name: /隔离回归轮次/}).click();
  const binding = page.getByRole("region", {name: "所属回归集"});
  await expect(binding).toContainText(/常规.*回归/);
  await expect(binding).toContainText("范围不完整");
  await page.reload();
  await expect(page.getByRole("region", {name: "所属回归集"})).toBeVisible();
});
