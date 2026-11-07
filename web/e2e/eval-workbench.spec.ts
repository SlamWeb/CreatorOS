import {expect, test} from "@playwright/test";

const round = (id: string, parent: string | null) => ({id, title: `版本 ${id}`, change_summary: `变更 ${id}`, code_version: `commit-${id}`, branch: "dev", merge_status: id === "r2" ? "unmerged" : "merged", parent_round_id: parent, created_at: "2026-11-07T00:00:00Z", changes: ["tools"]});
const run = (id: string, roundId: string) => ({id, round_id: roundId, suite_id: "工具调用", suite_version: "v1", status: "completed", planned_cases: ["T01", "T02"], created_at: "2026-11-07T00:00:00Z", started_at: null, finished_at: null, metadata: {suite_role: "specialist", change_scope: "tool", change_requirements: {rule_version: "change-scope-v1", coverage_known: true, required: [{id: "tool_calling", level: "专项评测", title: "工具选择"}, {id: "affected_chain", level: "完整链路", title: "受影响的链路"}], recommended: [{id: "common_regression", level: "常规回归", title: "常规全量回归"}]}}, results: [{case_id: "T01", status: roundId === "r2" ? "failed" : "passed", summary: `诊断 ${roundId}`, metrics: {input_tokens: 123}, evidence: {reason: `证据 ${roundId}`, user_requests: ["读取 {name}"], actual_user_requests: ["读取已展开名称"]}, case_definition_hash: "case-hash"}]});
const rounds = [round("r2", "r1"), round("r1", null)];

async function fixture(page: import("@playwright/test").Page) {
  await page.route("**/api/eval/v1/rounds", route => route.fulfill({json: {items: rounds}}));
  await page.route("**/api/eval/v1/rounds/r1", route => route.fulfill({json: {round: rounds[1], runs: [run("run1", "r1")]}}));
  await page.route("**/api/eval/v1/rounds/r2", route => route.fulfill({json: {round: rounds[0], runs: [run("run2", "r2")]}}));
  await page.route("**/api/eval/v1/compare?*", route => route.fulfill({json: {baseline_run_id: "run1", candidate_run_id: "run2", contract_compatible: true, contract_differences: [], baseline_planned: 2, candidate_planned: 2, common_planned: 2, comparable_cases: 1, outcomes: {paired_decidable: 1, baseline_passed: 1, candidate_passed: 0}, metrics: {input_tokens: {paired_cases: 1, baseline_mean: 140, candidate_mean: 123}, estimated_cost_min_usd: {paired_cases: 1, baseline_mean: 0.00001, candidate_mean: 0.00002}, estimated_cost_max_usd: {paired_cases: 1, baseline_mean: 0.00004, candidate_mean: 0.00005}}, cases: [{case_id: "T01", comparable: true, reason: null, baseline_status: "passed", candidate_status: "failed"}, {case_id: "T02", comparable: false, reason: "missing_result", baseline_status: "missing_result", candidate_status: "missing_result"}]}}));
}

test("shows actual recorded runs, paired comparison, and keeps selected case after refresh", async ({page}, info) => {
  await fixture(page);
  await page.goto("/eval");
  await expect(page.getByRole("heading", {name: "评测工作台"})).toBeVisible();
  await expect(page.getByRole("heading", {name: "版本 r2"})).toBeVisible();
  const requirements = page.getByRole("region", {name: "该运行登记的检查要求"});
  await expect(requirements).toContainText("运行 run2");
  await expect(requirements).toContainText("change-scope-v1");
  await expect(requirements).toContainText("已有专项运行");
  await expect(requirements).toContainText("受影响的链路");
  await expect(requirements).toContainText("未验证");
  await expect(requirements).toContainText("常规全量回归");
  await page.getByLabel("选择当前运行").selectOption("run2");
  await page.getByLabel("选择基准运行").selectOption("run1");
  await expect(page.getByRole("region", {name: "版本比较"})).toContainText("140 → 123");
  await expect(page.getByRole("region", {name: "版本比较"})).toContainText("双方同口径 1 题");
  await expect(page.getByText("Agent 费用估算（USD）· 每题均值").locator("..", {hasText: "估算区间"})).toContainText("$0.00001–$0.00004 → $0.00002–$0.00005");
  await expect(page.getByRole("region", {name: "测试结果"})).toContainText("1 失败 · 1 未记录/未确认");
  await page.getByRole("button", {name: /工具调用/}).click();
  await page.getByRole("button", {name: /T01/}).click();
  await expect(page.getByRole("region", {name: "运行详情"})).toContainText("诊断 r2");
  await expect(page.getByRole("region", {name: "运行详情"})).toContainText("读取已展开名称");
  await expect(page.locator(".ew-case-summary").first()).not.toContainText("读取 {name}");
  await page.getByText("评分依据与证据").click();
  await expect(page.locator(".ew-result-detail pre").last()).toContainText("证据 r2");
  await page.unroute("**/api/eval/v1/rounds");
  await page.route("**/api/eval/v1/rounds", route => route.fulfill({json: {items: [round("r3", "r2"), ...rounds]}}));
  await page.reload();
  await expect(page.getByRole("heading", {name: "版本 r2"})).toBeVisible();
  await expect(page.getByRole("region", {name: "运行详情"})).toContainText("诊断 r2");
  await page.screenshot({path: info.outputPath("eval-workbench-1440.png"), fullPage: true});
  await page.setViewportSize({width: 390, height: 844});
  await expect(page.getByLabel("选择轮次")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path: info.outputPath("eval-workbench-390.png"), fullPage: true});
});

test("shows empty and failed reads rather than inventing results", async ({page}) => {
  await page.route("**/api/eval/v1/rounds", route => route.fulfill({json: {items: []}}));
  await page.goto("/eval");
  await expect(page.getByText("还没有正式评测记录")).toBeVisible();
  await expect(page.getByRole("button", {name: /查看历史 Benchmark/})).toBeVisible();
  await page.unroute("**/api/eval/v1/rounds");
  await page.route("**/api/eval/v1/rounds", route => route.fulfill({status: 503, json: {error: {message: "登记处不可用"}}}));
  await page.reload();
  await expect(page.getByRole("alert")).toContainText("登记处不可用");
});

test("does not manufacture a cost interval when one paired bound is missing", async ({page}) => {
  await fixture(page);
  await page.route("**/api/eval/v1/compare?*", route => route.fulfill({json: {baseline_run_id: "run1", candidate_run_id: "run2", contract_compatible: true, contract_differences: [], baseline_planned: 1, candidate_planned: 1, common_planned: 1, comparable_cases: 1, outcomes: {paired_decidable: 1, baseline_passed: 1, candidate_passed: 1}, metrics: {estimated_cost_min_usd: {paired_cases: 1, baseline_mean: 0.00001, candidate_mean: 0.00002}}, cases: []}}));
  await page.goto("/eval");
  await page.getByLabel("选择当前运行").selectOption("run2");
  await page.getByLabel("选择基准运行").selectOption("run1");
  await expect(page.getByText("Agent 费用估算（USD）· 每题均值").locator("..")).toContainText("未记录");
});

test("shows unknown or missing requirement scope as unverified", async ({page}) => {
  await fixture(page);
  const unknown = run("run2", "r2");
  unknown.metadata.change_requirements.coverage_known = false;
  await page.route("**/api/eval/v1/rounds/r2", route => route.fulfill({json: {round: rounds[0], runs: [unknown]}}));
  await page.goto("/eval");
  await expect(page.getByRole("region", {name: "该运行登记的检查要求"})).toContainText("改动范围未完整识别");
  const noRequirements = run("run2", "r2");
  await page.unroute("**/api/eval/v1/rounds/r2");
  await page.route("**/api/eval/v1/rounds/r2", route => route.fulfill({json: {round: rounds[0], runs: [{...noRequirements, metadata: {}}]}}));
  await page.reload();
  await expect(page.getByRole("region", {name: "该运行登记的检查要求"})).toContainText("未记录检查要求");
});

test("distinguishes absent results from explicitly unrun cases", async ({page}) => {
  await fixture(page);
  let current = {...run("run2", "r2"), status: "running", display_status: "running"};
  await page.route("**/api/eval/v1/rounds/r2", route => route.fulfill({json: {round: rounds[0], runs: [current]}}));
  await page.goto("/eval");
  await expect(page.getByRole("region", {name: "测试结果"})).toContainText("1 失败 · 1 暂无结果");
  await page.getByRole("button", {name: /工具调用/}).click();
  await page.getByRole("button", {name: /T02/}).click();
  await expect(page.getByRole("region", {name: "运行详情"})).toContainText("暂无结果。");
  current = {...current, display_status: "unknown"};
  await page.reload();
  await expect(page.getByRole("region", {name: "运行详情"})).toContainText("暂无结果/状态未知");
  current = {...current, status: "completed", display_status: "completed"};
  await page.reload();
  await expect(page.getByRole("region", {name: "运行详情"})).toContainText("未记录/未确认");
  current = {...current, results: [...current.results, {...current.results[0], case_id: "T02", status: "not_run"}]};
  await page.reload();
  await expect(page.getByRole("region", {name: "运行详情"})).toContainText("未运行");
  await expect(page.getByRole("region", {name: "运行详情"})).not.toContainText("未记录/未确认");
});

test("uses each frozen selection for case identity and leaves ambiguous or legacy IDs unguessed", async ({page}) => {
  await fixture(page);
  const base = run("run1", "r1"), current = run("run2", "r2");
  const withCase = (entry: ReturnType<typeof run>, selection: unknown) => ({...entry,
    planned_cases: ["B06", "B07"],
    metadata: {...entry.metadata, catalog_binding: {selected_instance_ids: selection}},
    results: [{...entry.results[0], case_id: "B06"}]});
  let now = withCase(current, ["natural:B06", "base:B07", "natural:B07"]);
  await page.route("**/api/eval/v1/rounds/r1", route => route.fulfill({json: {round: rounds[1], runs: [withCase(base, ["base:B06"])]}}));
  await page.route("**/api/eval/v1/rounds/r2", route => route.fulfill({json: {round: rounds[0], runs: [now]}}));
  await page.route("**/api/eval/v1/compare?*", route => route.fulfill({json: {baseline_run_id: "run1", candidate_run_id: "run2", contract_compatible: false, contract_differences: [], baseline_planned: 1, candidate_planned: 2, common_planned: 1, comparable_cases: 0, outcomes: {paired_decidable: 0, baseline_passed: 0, candidate_passed: 0}, metrics: {}, cases: [{case_id: "B06", comparable: false, reason: "dataset differs", baseline_status: "passed", candidate_status: "failed"}]}}));
  await page.goto("/eval?round=r2&run=run2");
  const list = page.getByLabel("题目列表");
  await expect(list).toContainText("natural:B06");
  await expect(list).toContainText("B07 · 身份未确认");
  await list.getByRole("button", {name: /natural:B06/}).click();
  await expect(page.getByRole("heading", {name: "natural:B06"})).toBeVisible();
  await page.getByLabel("选择当前运行").selectOption("run2");
  await page.getByLabel("选择基准运行").selectOption("run1");
  await page.getByText("查看逐题与比较依据").click();
  await expect(page.locator(".ew-compare-details")).toContainText("基准 base:B06 / 当前 natural:B06");
  await expect(page.locator(".ew-compare-details")).toContainText("不可比（题目身份未匹配）");
  now = {...now, metadata: {...current.metadata, catalog_binding: undefined}};
  await page.reload();
  await expect(list.getByRole("button", {name: /B06/})).toContainText("B06");
  await expect(list).not.toContainText("natural:B06");
  await expect(list).not.toContainText("身份未确认");
});
