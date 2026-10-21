import { expect, test } from "@playwright/test";

const id = "b".repeat(64);
const image = `/api/skill-iterations/${id}/files/test-image`;
const baseline = () => ({ id, creator_id: "account-iteration", series_id: "series-iteration", series_name: "柯基故事", skill_id: "comic-v1", skill_name: "柯基漫画",
  topic_id: "topic-iteration", topic_title: "下班后不再为一点小事反复内耗", status: "paused", stage: "judge", revision: 3, round_number: 2, max_rounds: 3,
  judge: { version: 2, threshold: 80, critical_floor: 60, criteria: [
    { id: "story", description: "故事用动作而非说教完成转折", evidence: "参考作品最后用放下手机的动作收束。", critical: true },
    { id: "light", description: "画面不过亮，保留安静的阴影", evidence: "参考图室内是低饱和暖光。", critical: false }],
    history: [{ version: 1, added_ids: ["story"], reason: "由参考作品建立基础准则", at: "2026-10-22T01:00:00Z" },
      { version: 2, added_ids: ["light"], reason: "首轮画面过亮，追加后用同一准则重评全部产物。", at: "2026-10-22T01:10:00Z" }] },
  rounds: [1, 2].map(number => ({ number, skill_id: `comic-v${number}`, version_number: number, digest: `digest-${number}`, status: "completed",
    trial_url: `/skills?extraction=trial-${number}`, images: [{ order: 1, url: image, alt: `第 ${number} 轮产物` }], score: number === 1 ? 54 : 76, passed: false,
    judge_version: 2, feedback: number === 1 ? "结尾仍使用长篇劝解，画面比参考过亮。" : "画面亮度改善，故事转折仍需要更具体的动作。",
    changes: number === 1 ? "保留原 Skill，建立基线。" : "限定结尾以动作收束，不添加价值观总结。",
    evaluations: number === 1 ? [{ judge_version: 1, score: 62, passed: false,
      scores: [{ criterion_id: "story", score: 62, evidence: "旧准则仅检查故事转折。" }], feedback: "原准则尚未覆盖画面明亮度。", time: "2026-10-22T01:05:00Z" },
    { judge_version: 2, score: 54, passed: false, scores: [{ criterion_id: "light", score: 58, evidence: "新增亮度准则后统一补评。" }], feedback: "新准则补评后，画面明亮度仍未达到参考。", time: "2026-10-22T01:10:00Z" }] : [],
    scores: [{ criterion_id: "story", score: number === 1 ? 50 : 75, evidence: "最后一格仍出现解释性的长旁白。" },
      { criterion_id: "light", score: number === 1 ? 58 : 77, evidence: "室内阴影已更接近参考。" }] })),
  best_skill_id: "comic-v2", best_round: 2, best_score: 76, error: null, can_pause: false, can_resume: true, can_continue: false,
  usage: { input_tokens: 14000, output_tokens: 2000, calls: 4, has_unknown_usage: true }, events: [{ time: "2026-10-22T01:11:00Z", kind: "paused", message: "用户暂停，已完成产物保留。", details: { task_id: id } }],
  url: `/skills?iteration=${id}`, created_at: "2026-10-22T01:00:00Z", updated_at: "2026-10-22T01:11:00Z" });

// Controlled UI contracts and failure injection, not a real model or content-quality Eval.
test.beforeEach(async ({ page, request }) => {
  const fixture = await request.post("/test/skill-files", { data: {} }).then(response => response.json());
  const bytes = await request.get(`/api/producer-skills/${fixture.mind.id}/files/content?path=assets%2Fdiagram.png`).then(response => response.body());
  await page.route(`**${image}`, route => route.fulfill({ contentType: "image/png", body: bytes }));
});
test("RSI displays versioned judging, full round evidence and safe pause/resume controls", async ({ page }, testInfo) => {
  let doc = baseline();
  const writes: Record<string, unknown>[] = [];
  await page.route(`**/api/skill-iterations/${id}`, route => route.fulfill({ json: doc }));
  await page.route(`**/api/skill-iterations/${id}/actions`, route => {
    const input = route.request().postDataJSON(); writes.push(input);
    doc = { ...doc, status: input.action === "pause" ? "paused" : "waiting_production", revision: doc.revision + 1,
      can_pause: input.action !== "pause", can_resume: input.action === "pause" };
    return route.fulfill({ json: doc });
  });
  await page.goto(`/skills?iteration=${id}`);
  await expect(page.getByRole("heading", { name: "柯基漫画", exact: true })).toBeVisible();
  await expect(page.getByText("只交付候选，不自动切换栏目", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "第 1 轮 · 基线" })).toBeVisible();
  await expect(page.getByRole("img", { name: "第 2 轮产物" })).toBeVisible();
  await expect(page.getByText("76.0 / 100", { exact: true })).toBeVisible();
  await expect(page.getByRole("complementary", { name: "Judge 准则" })).toContainText("总分 ≥ 80；关键项 ≥ 60");
  await page.getByRole("button", { name: "准则变更记录" }).click();
  await expect(page.getByText("首轮画面过亮，追加后用同一准则重评全部产物。", { exact: true })).toBeVisible();
  await expect(page.getByText(`任务 ${id}`, { exact: false })).toHaveCount(0);
  await page.getByRole("button", { name: "技术记录", exact: true }).click();
  await expect(page.getByText("DS 用量 · 模型请求 4 次 · 输入 14,000 · 输出 2,000 tokens", { exact: true })).toBeVisible();
  await expect(page.getByText("部分调用用量未记录", { exact: true })).toBeVisible();
  await expect(page.getByText("Codex 用量请查看各轮完整试产记录。", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "技术记录", exact: true }).click();
  const history = page.getByRole("button", { name: "历次评分", exact: true });
  await expect(history).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByText("原准则尚未覆盖画面明亮度。", { exact: true })).toHaveCount(0);
  await history.click();
  await expect(page.getByRole("heading", { name: "Judge v1 · 62.0 / 100", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Judge v2 · 54.0 / 100", exact: true })).toBeVisible();
  await expect(page.getByText("旧准则仅检查故事转折。", { exact: true })).toBeVisible();
  await expect(page.getByText("新增亮度准则后统一补评。", { exact: true })).toBeVisible();
  await expect(page.getByText("原准则尚未覆盖画面明亮度。", { exact: true })).toBeVisible();
  await history.click();
  await page.getByRole("button", { name: "恢复", exact: true }).click();
  await expect(page.getByRole("button", { name: "暂停", exact: true })).toBeVisible();
  expect(writes[0]).toMatchObject({ action: "resume", expected_revision: 3 });
  await page.getByRole("button", { name: "暂停", exact: true }).click();
  await expect(page.getByRole("button", { name: "恢复", exact: true })).toBeVisible();
  expect(writes[1]).toMatchObject({ action: "pause", expected_revision: 4 });
  await page.reload();
  await expect(page.getByRole("button", { name: "恢复", exact: true })).toBeVisible();
  expect(writes).toHaveLength(2);
  await page.screenshot({ path: testInfo.outputPath("iteration-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("heading", { name: "评判准则", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("iteration-mobile.png"), fullPage: true });
  await page.reload();
  const judgeToggle = page.getByRole("button", { name: "评判准则", exact: true });
  await expect(judgeToggle).toHaveAttribute("aria-expanded", "false");
  await judgeToggle.click();
  await expect(page.getByRole("button", { name: "准则变更记录" })).toBeVisible();
});

test("unknown RSI action survives reload and explicitly retries the same identity without production calls", async ({ page }) => {
  let doc = baseline();
  const writes: Record<string, unknown>[] = [];
  let otherWrites = 0;
  page.on("request", request => { if (["POST", "PUT", "DELETE"].includes(request.method()) && !request.url().endsWith(`/skill-iterations/${id}/actions`)) otherWrites++; });
  await page.route(`**/api/skill-iterations/${id}`, route => route.fulfill({ json: doc }));
  await page.route(`**/api/skill-iterations/${id}/actions`, async route => {
    writes.push(route.request().postDataJSON());
    if (writes.length === 1) await route.abort("failed");
    else { doc = { ...doc, status: "waiting_production", revision: 4, can_resume: false, can_pause: true }; await route.fulfill({ json: doc }); }
  });
  await page.goto(`/skills?iteration=${id}`);
  await page.getByRole("button", { name: "恢复", exact: true }).click();
  await expect(page.getByRole("button", { name: "核对并恢复上次操作" })).toBeVisible();
  await page.reload();
  expect(writes).toHaveLength(1);
  await expect(page.getByRole("button", { name: "恢复", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "核对并恢复上次操作" }).click();
  await expect(page.getByRole("button", { name: "暂停", exact: true })).toBeVisible();
  expect(writes[1]).toEqual(writes[0]);
  expect(otherWrites).toBe(0);
});

test("stale action and failed reads remain visible without automatic authorization", async ({ page }) => {
  let readsFail = true;
  const doc = { ...baseline(), status: "exhausted", can_resume: false, can_continue: true };
  let posts = 0;
  await page.route(`**/api/skill-iterations/${id}`, route => readsFail ? route.fulfill({ status: 503, json: { error: { message: "任务读取暂时不可用" } } }) : route.fulfill({ json: doc }));
  await page.route(`**/api/skill-iterations/${id}/actions`, route => { posts++; return route.fulfill({ status: 409, json: { error: { message: "任务状态已改变" } } }); });
  await page.goto(`/skills?iteration=${id}`);
  await expect(page.getByRole("alert")).toContainText("任务读取暂时不可用");
  readsFail = false;
  await page.getByRole("button", { name: "重新读取", exact: true }).click();
  await expect(page.getByRole("button", { name: "再试 3 轮", exact: true })).toBeVisible();
  page.once("dialog", dialog => dialog.dismiss());
  await page.getByRole("button", { name: "再试 3 轮", exact: true }).click();
  expect(posts).toBe(0);
  page.once("dialog", dialog => dialog.accept());
  await page.getByRole("button", { name: "再试 3 轮", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("任务状态已改变");
  expect(posts).toBe(1);
  await expect(page.getByRole("button", { name: "核对并恢复上次操作" })).toHaveCount(0);
});

test("RSI history loads on demand and opens the same task without starting another", async ({ page }) => {
  let lists = 0, posts = 0;
  await page.route("**/api/skill-iterations", route => { lists++; return route.fulfill({ json: { items: [baseline()] } }); });
  await page.route(`**/api/skill-iterations/${id}`, route => route.fulfill({ json: baseline() }));
  page.on("request", request => { if (request.method() === "POST") posts++; });
  await page.goto("/skills");
  const toggle = page.getByRole("button", { name: "RSI 迭代记录" });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(lists).toBe(0);
  await toggle.click();
  await page.getByRole("link", { name: /柯基漫画.*下班后/ }).click();
  await expect(page).toHaveURL(new RegExp(`iteration=${id}`));
  await expect(page.getByRole("heading", { name: "柯基漫画", exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "柯基漫画", exact: true })).toBeVisible();
  expect(posts).toBe(0);
});
