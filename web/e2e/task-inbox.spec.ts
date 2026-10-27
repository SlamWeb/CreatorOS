import { expect, test } from "@playwright/test";

test("waiting video remains findable in inbox, account summary and Observation without a Run", async ({ page, request }, testInfo) => {
  const response = await request.post("/test/waiting-task", { data: {} });
  expect(response.ok(), await response.text()).toBeTruthy();
  const seed = await response.json();
  const taskCount = async () => (await (await request.get(`/api/topics/${seed.topic_id}/production-tasks`)).json()).items.length;
  const before = await taskCount();
  await page.goto("/skills");
  await page.getByRole("button", { name: "任务", exact: true }).click();
  const panel = page.getByRole("dialog", { name: "任务", exact: true });
  const row = panel.locator(".work-inbox-row").filter({ hasText: seed.title });
  await expect(row).toContainText("等待材料");
  await expect(panel.getByRole("link", { name: `素材集：${seed.collection_name}`, exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("waiting-inbox-desktop.png"), fullPage: true });
  await row.click();
  await expect(page).toHaveURL(new RegExp(`topic=${seed.topic_id}`));
  await page.goto(`/?creator=${seed.creator_id}&series=${seed.series_id}`);
  await page.getByRole("button", { name: "查看任务", exact: true }).click();
  await expect(page.locator(".creator-task-summary")).toContainText(seed.title);
  await expect(page.locator(".creator-task-summary")).toContainText("等待材料");
  await page.goto(`/observation?node=${seed.observation_node}`);
  await expect(page.locator(".observation-page")).toContainText(seed.title);
  await expect(page.getByRole("link", { name: `素材集：${seed.collection_name}`, exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/skills");
  await page.reload();
  await page.getByRole("button", { name: "任务", exact: true }).click();
  await expect(panel.locator(".work-inbox-row").filter({ hasText: seed.title })).toContainText("等待材料");
  const bounds = await panel.boundingBox();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
  await page.screenshot({ path: testInfo.outputPath("waiting-inbox-mobile.png"), fullPage: true });
  await panel.getByRole("link", { name: `素材集：${seed.collection_name}`, exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`collection=${seed.collection_id}`));
  expect(await taskCount()).toBe(before);
});

test("task inbox finds old and new work, keeps viewed versions across refresh without business writes", async ({ page, request }, testInfo) => {
  const old = await (await request.post("/test/calibration", { data: {} })).json();
  await page.goto("/skills");
  await expect.poll(() => page.evaluate(() => Boolean(localStorage.getItem("creatoros:work-inbox:seen:v1")))).toBe(true);
  const trigger = page.getByRole("button", { name: "任务", exact: true });
  await expect(trigger.getByRole("img", { name: "有未查看的任务" })).toHaveCount(0);
  await trigger.click();
  const panel = page.getByRole("dialog", { name: "任务", exact: true });
  await expect(panel.locator(`a[href="/runs/${old.run_id}"]`)).toBeVisible();
  await panel.getByRole("button", { name: "关闭任务" }).click();

  // These are isolated real SQLite/files fixtures, not fabricated inbox responses.
  const fresh = await (await request.post("/test/calibration", { data: {} })).json();
  const evidenceBefore = await (await request.get("/test/calibration-evidence")).json();
  await trigger.click();
  const target = panel.locator(`a[href="/runs/${fresh.run_id}"]`);
  await expect(target).toBeVisible();
  await expect(target.getByRole("img", { name: "有未查看内容" })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("task-inbox-desktop.png"), fullPage: true });
  await target.click();
  await expect(page).toHaveURL(new RegExp(`/runs/${fresh.run_id}`));
  await page.reload();
  await page.getByRole("button", { name: "任务", exact: true }).click();
  await expect(panel.locator(`a[href="/runs/${fresh.run_id}"]`).getByRole("img", { name: "有未查看内容" })).toHaveCount(0);
  const evidenceAfter = await (await request.get("/test/calibration-evidence")).json();
  expect(evidenceAfter.counts).toEqual(evidenceBefore.counts);
  expect(evidenceAfter.target_files).toEqual(evidenceBefore.target_files);
  expect(evidenceAfter.companion_files).toEqual(evidenceBefore.companion_files);
  expect(evidenceAfter.artifact_files).toEqual(evidenceBefore.artifact_files);
});

test("inbox retains existing work on refresh error and supports keyboard/mobile dismissal", async ({ page, request }, testInfo) => {
  const seed = await (await request.post("/test/calibration", { data: {} })).json();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/skills");
  await page.getByRole("button", { name: "任务", exact: true }).click();
  const panel = page.getByRole("dialog", { name: "任务", exact: true });
  await expect(panel.locator(`a[href="/runs/${seed.run_id}"]`)).toBeVisible();
  // Explicit network failure injection only; successful index data uses the real API.
  await page.route("**/api/work-inbox?limit=50", route => route.abort("connectionfailed"));
  await panel.getByRole("button", { name: "刷新任务" }).click();
  await expect(panel.getByRole("alert")).toContainText("刷新失败");
  await expect(panel.locator(`a[href="/runs/${seed.run_id}"]`)).toBeVisible();
  await page.unroute("**/api/work-inbox?limit=50");
  await panel.getByRole("button", { name: "刷新任务" }).click();
  await expect(panel.getByRole("alert")).toHaveCount(0);
  const bounds = await panel.boundingBox();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
  await page.screenshot({ path: testInfo.outputPath("task-inbox-mobile.png"), fullPage: true });
  await page.keyboard.press("Escape");
  await expect(panel).toHaveCount(0);
  await expect(page.getByRole("button", { name: "任务", exact: true })).toBeFocused();
});

test("new saved Skill keeps its dot until its actual details are opened, including direct skill links", async ({ page, request }) => {
  await page.goto("/skills");
  await expect.poll(() => page.evaluate(() => Boolean(localStorage.getItem("creatoros:work-inbox:seen:v1")))).toBe(true);
  const key = crypto.randomUUID().replaceAll("-", "");
  const name = `inbox-story-${key.slice(0, 12)}`;
  // Real authored draft/save is test setup; no model or automatic production is involved.
  const authoredResponse = await request.post("/api/skill-extractions/author", { data: {
    request_id: `inbox-author-${key}`, name, display_name: `收件箱故事 ${key.slice(0, 6)}`,
    description: "隔离验证的手写故事 Skill", content: "用开头的道具完成结尾的行动。", role: "mind", upload_ids: [],
  } });
  expect(authoredResponse.status()).toBe(201);
  const authored = await authoredResponse.json();
  const savedResponse = await request.post(`/api/skill-extractions/${authored.id}/save`, { data: { expected_digest: authored.digest } });
  expect(savedResponse.status()).toBe(200);
  const saved = await savedResponse.json();
  const skillId = saved.saved_skills[0].id;
  const before = await (await request.get("/test/composer-evidence")).json();
  await page.reload();
  const card = page.locator("button.skill-card").filter({ hasText: `收件箱故事 ${key.slice(0, 6)}` });
  await expect(card).toBeVisible();
  await expect(card.getByRole("img", { name: "Skill 有未查看更新" })).toBeVisible();
  // Viewing only the task list is not acknowledgement of the Skill itself.
  await page.getByRole("button", { name: "任务", exact: true }).click();
  await page.keyboard.press("Escape");
  await expect(card.getByRole("img", { name: "Skill 有未查看更新" })).toBeVisible();
  await page.goto(`/skills?skill=${encodeURIComponent(skillId)}`);
  const details = page.getByRole("dialog").filter({ has: page.getByRole("heading", { name: `收件箱故事 ${key.slice(0, 6)}` }) });
  await expect(details).toBeVisible();
  await details.getByRole("button", { name: "关闭 Skill 详情" }).click();
  await expect(card.getByRole("img", { name: "Skill 有未查看更新" })).toHaveCount(0);
  await page.reload();
  await expect(card.getByRole("img", { name: "Skill 有未查看更新" })).toHaveCount(0);
  const after = await (await request.get("/test/composer-evidence")).json();
  expect(after.counts).toEqual(before.counts);
  expect(after.files).toEqual(before.files);
  expect(after.execution_counts).toEqual(before.execution_counts);
});
