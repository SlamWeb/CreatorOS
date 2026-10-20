import { expect, test, type Page } from "@playwright/test";

// Real isolated SQLite/files/HTTP; the fixture explicitly controls only the
// expensive reviser/producer boundary. This is not a content quality Eval.
type Setup = { series_id: string; skill_id: string; companion_id: string; run_id: string; revision_id: string; skill_digest: string; reference_upload: { id: string; url: string }; };
async function seed(page: Page) {
  const response = await page.request.post("/test/calibration", { data: {} });
  expect(response.ok()).toBeTruthy();
  return response.json() as Promise<Setup>;
}
async function evidence(page: Page) { const response = await page.request.get("/test/calibration-evidence"); expect(response.ok()).toBeTruthy(); return response.json(); }
async function fillComparison(page: Page, setup: Setup, upload = true) {
  await page.goto(`/runs/${setup.run_id}?revision=${setup.revision_id}`);
  await page.getByRole("link", { name: "改进 Skill", exact: true }).click();
  await expect(page.getByRole("heading", { name: "改进 Skill", exact: true })).toBeVisible();
  await page.getByLabel("要改进的 Skill", { exact: true }).selectOption(setup.skill_id);
  await page.getByLabel("产物具体评价", { exact: true }).fill("结尾只讲道理，没有用开头道具完成具体行动。");
  await page.getByRole("button", { name: "加入对照", exact: true }).click();
  await expect(page.getByLabel("已加入的产物评价")).toContainText("结尾只讲道理");
  if (upload) {
    const reference = await page.request.get(setup.reference_upload.url);
    expect(reference.ok()).toBeTruthy();
    await page.getByLabel("参考图片", { exact: true }).setInputFiles({ name: "值得借鉴.png", mimeType: "image/png", buffer: await reference.body() });
    await page.getByLabel("参考图 1 具体评价", { exact: true }).fill("结尾用开头的道具送给朋友，保留这个行动回扣。");
  }
}
async function generate(page: Page) {
  const accepted = page.waitForResponse(response =>
    new URL(response.url()).pathname === "/api/skill-calibrations" && response.request().method() === "POST");
  await page.getByRole("button", { name: "生成修改草稿", exact: true }).click();
  const response = await accepted;
  expect(response.status(), await response.text()).toBe(202);
  await expect(page).toHaveURL(/\/skills\?extraction=.*calibration=/);
  await expect(page.getByLabel("编辑 SKILL.md")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "编辑", exact: true })).toBeVisible({ timeout: 15000 });
  return new URL(page.url()).searchParams.get("calibration")!;
}
async function trial(page: Page) {
  const existing = await page.locator(".extraction-trial").count();
  await page.getByLabel("试产选题", { exact: true }).fill("第一次做蛋糕被别人比下去，最后把蛋糕送给朋友。");
  await page.getByRole("button", { name: "主动开始试产", exact: true }).click();
  await expect(page.locator(".extraction-trial")).toHaveCount(existing + 1, { timeout: 20000 });
  await expect(page.locator(".extraction-trial").last().getByText("试产完成", { exact: true })).toBeVisible({ timeout: 20000 });
}

test("calibration compares real artifacts and references, edits and trials a candidate, then explicitly updates the original", async ({ page }, info) => {
  const setup = await seed(page); const before = await evidence(page);
  await fillComparison(page, setup);
  await page.getByRole("group", { name: "产物评价", exact: true }).getByRole("button", { name: "好在哪里", exact: true }).click();
  await page.getByLabel("产物具体评价", { exact: true }).fill("开场的蛋糕道具明确，值得保留。");
  await page.getByRole("button", { name: "加入对照", exact: true }).click();
  await expect(page.getByLabel("已加入的产物评价").locator("article")).toHaveCount(2);
  await page.getByLabel("校准其他要求", { exact: true }).fill("只改故事方法，保留角色和画风。");
  await page.screenshot({ path: info.outputPath("comparison-desktop.png"), fullPage: true });
  const id = await generate(page);
  await expect(page.getByRole("button", { name: "确认加入 Skill 库", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "确认更新当前 Skill", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: /执行过程/ })).toHaveAttribute("aria-expanded", "false");
  const explanation = page.getByRole("button", { name: /修改说明/ });
  await expect(explanation).toHaveAttribute("aria-expanded", "false");
  await explanation.click();
  await expect(page.getByText(/这里验证长说明默认折叠/)).toBeVisible();
  await explanation.click();
  await page.getByRole("button", { name: /修改差异与依据/ }).click();
  await expect(page.getByLabel("Skill 修改差异")).toContainText("受控校准改稿");
  expect((await evidence(page)).target_digest).toBe(before.target_digest);
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  const editor = page.getByLabel("编辑 SKILL.md", { exact: true });
  await editor.fill(`${await editor.inputValue()}\n测试者手工校准：保留蛋糕，不改成空泛口号。\n\n[查看参考作品](assets/reference.png)\n\n![内嵌参考图](assets/reference.png)\n`);
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.getByText("有未保存的草稿修改", { exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Skill 修改差异")).toContainText("测试者手工校准：保留蛋糕");
  const embedded = page.getByRole("img", { name: "内嵌参考图", exact: true });
  await expect(embedded).toBeVisible();
  await expect.poll(() => embedded.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
  await page.getByRole("button", { name: "查看参考作品", exact: true }).click();
  const asset = page.getByRole("img", { name: "assets/reference.png", exact: true });
  await expect(asset).toBeVisible();
  expect((await page.request.get((await asset.getAttribute("src"))!)).status()).toBe(200);
  await page.getByRole("navigation", { name: "Skill 文件", exact: true }).getByRole("button", { name: "SKILL.md", exact: true }).click();
  await trial(page);
  const afterTrial = await evidence(page);
  expect(afterTrial.target_digest).toBe(before.target_digest);
  expect(afterTrial.companion_files).toEqual(before.companion_files);
  expect(afterTrial.artifact_files).toEqual(before.artifact_files);
  expect(afterTrial.counts).toEqual(before.counts);
  expect(afterTrial.protected_records).toEqual(before.protected_records);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("draft-trial-mobile.png"), fullPage: true });
  await page.getByRole("button", { name: "确认更新当前 Skill", exact: true }).click();
  await expect(page.getByLabel("确认更新共享 Skill")).toContainText("[隔离验证] 故事校准");
  await page.getByRole("button", { name: "试用满意，确认保存", exact: true }).click();
  await expect(page.getByText("已确认更新", { exact: true })).toBeVisible();
  const after = await evidence(page);
  expect(after.target_digest).not.toBe(before.target_digest);
  expect(after.companion_files).toEqual(before.companion_files);
  expect(after.artifact_files).toEqual(before.artifact_files);
  expect(after.counts).toEqual(before.counts);
  expect(after.protected_records).toEqual(before.protected_records);
  const result = await (await page.request.get(`/api/skill-calibrations/${id}`)).json();
  expect(result.status).toBe("applied");
  expect(result.skill_id).toBe(setup.skill_id);
  expect(result.job.trials[0].thread_id).toContain("controlled-calibration-trial-");
  expect(result.job.trials[0].digest).toBe(result.job.digest);
  await page.reload();
  await expect(page.getByText("已确认更新", { exact: true })).toBeVisible();
});

test("late old calibration response cannot replace a newer saved draft and diff", async ({ page }) => {
  // Deterministic stale-network injection, not a model or quality test.
  const setup = await seed(page);
  await fillComparison(page, setup, false);
  const id = await generate(page);
  const endpoint = `/api/skill-calibrations/${id}`;
  await page.getByRole("button", { name: /修改差异与依据/ }).click();
  await expect(page.getByLabel("Skill 修改差异")).toContainText("受控校准改稿");
  const old = await (await page.request.get(endpoint)).json();
  let release!: () => void;
  let captured!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  const received = new Promise<void>(resolve => { captured = resolve; });
  let delayed = false;
  let delivered = false;
  await page.route(`**${endpoint}`, async route => {
    if (!delayed) {
      delayed = true;
      captured();
      await held;
      await route.fulfill({ status: 200, json: old });
      delivered = true;
    } else {
      await route.continue();
    }
  });
  // The first edit starts a refresh that is held behind the second edit.
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  const editor = page.getByLabel("编辑 SKILL.md", { exact: true });
  await editor.fill(`${await editor.inputValue()}\n中间稿：还没保存最后要求。\n`);
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await received;
  await expect(page.getByText("有未保存的草稿修改", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await editor.fill(`${await editor.inputValue()}\n新稿必须保留：旧请求不准覆盖这个句子。\n`);
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.getByText("有未保存的草稿修改", { exact: true })).toHaveCount(0);
  const diff = page.getByLabel("Skill 修改差异");
  await expect(diff).toContainText("新稿必须保留：旧请求不准覆盖");
  // Observe every repaint after the new result is visible, not just eventual consistency.
  await diff.evaluate(element => {
    const win = window as typeof window & { calibrationRegressed?: boolean; calibrationObserver?: MutationObserver };
    win.calibrationRegressed = false;
    win.calibrationObserver = new MutationObserver(() => {
      if (!element.textContent?.includes("新稿必须保留：旧请求不准覆盖")) win.calibrationRegressed = true;
    });
    win.calibrationObserver.observe(element, { childList: true, subtree: true, characterData: true });
  });
  release();
  await expect.poll(() => delivered).toBe(true);
  await expect(diff).toContainText("新稿必须保留：旧请求不准覆盖");
  expect(await page.evaluate(() => {
    const win = window as typeof window & { calibrationRegressed?: boolean; calibrationObserver?: MutationObserver };
    win.calibrationObserver?.disconnect();
    return win.calibrationRegressed;
  })).toBe(false);
  const current = await (await page.request.get(endpoint)).json();
  expect(current.job.revision).toBeGreaterThan(old.job.revision);
  expect(current.job.digest).not.toBe(old.job.digest);
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await expect(editor).toHaveValue(/新稿必须保留：旧请求不准覆盖/);
});

test("changing the candidate invalidates the old trial, and a shared source conflict is never force overwritten", async ({ page }) => {
  const setup = await seed(page);
  await fillComparison(page, setup, false); await generate(page); await trial(page);
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  const editor = page.getByLabel("编辑 SKILL.md", { exact: true });
  await editor.fill(`${await editor.inputValue()}\n改稿后的新版本。\n`);
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.getByRole("button", { name: "确认更新当前 Skill", exact: true })).toBeDisabled();
  await expect(page.getByText("旧草稿结果", { exact: true })).toBeVisible();
  await trial(page);
  const sourceResponse = await page.request.get(`/api/producer-skills/${encodeURIComponent(setup.skill_id)}/files/content?path=SKILL.md`);
  const source = await sourceResponse.json();
  const concurrent = await page.request.put(`/api/producer-skills/${encodeURIComponent(setup.skill_id)}/files/content`, { data: { path: "SKILL.md", content: `${source.content}\n另一位编辑的新要求。\n`, expected_digest: source.digest } });
  expect(concurrent.ok()).toBeTruthy();
  const changed = await evidence(page);
  await page.getByRole("button", { name: "确认更新当前 Skill", exact: true }).click();
  await page.getByRole("button", { name: "试用满意，确认保存", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "保存未确认" })).toBeVisible();
  await expect(page.getByRole("button", { name: "重新确认保存", exact: true })).toHaveCount(0);
  expect((await evidence(page)).target_digest).toBe(changed.target_digest);
});

test("only a confirmed rollback permits a new explicit save request without another trial", async ({ page }) => {
  // Controlled UI fault injection: the first save does not reach the writer;
  // its GET projection simulates a backend-verified rollback. Real rollback
  // filesystem/SQLite behavior is covered by the backend fault tests.
  const setup = await seed(page);
  await fillComparison(page, setup, false);
  const id = await generate(page);
  await trial(page);
  const before = await evidence(page);
  const requestIds: string[] = [];
  let rolledBack = false;
  await page.route(`**/api/skill-calibrations/${id}/apply`, async route => {
    requestIds.push(route.request().postDataJSON().request_id);
    if (requestIds.length === 1) {
      rolledBack = true;
      await route.fulfill({ status: 409, json: { detail: "受控故障：保存已回滚，原 Skill 保留。" } });
    } else { rolledBack = false; await route.continue(); }
  });
  await page.route(`**/api/skill-calibrations/${id}`, async route => {
    const response = await route.fetch(); const body = await response.json();
    await route.fulfill({ response, json: rolledBack ? { ...body, status: "ready", apply_phase: "rolled_back", apply_request_id: requestIds[0] } : body });
  });
  await page.getByRole("button", { name: "确认更新当前 Skill", exact: true }).click();
  await page.getByRole("button", { name: "试用满意，确认保存", exact: true }).click();
  const recover = page.getByRole("button", { name: "重新确认保存", exact: true });
  await expect(recover).toBeVisible();
  expect((await evidence(page)).target_digest).toBe(before.target_digest);
  await recover.click();
  expect(requestIds).toHaveLength(1);
  await expect(page.getByRole("group", { name: "确认更新共享 Skill", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "试用满意，确认保存", exact: true }).click();
  await expect(page.getByText("已确认更新", { exact: true })).toBeVisible();
  expect(requestIds).toHaveLength(2);
  expect(requestIds[1]).not.toBe(requestIds[0]);
  const result = await (await page.request.get(`/api/skill-calibrations/${id}`)).json();
  expect(result.job.trials).toHaveLength(1);
});

test("an accepted but lost create response survives reload and restores exactly the same calibration", async ({ page }) => {
  const setup = await seed(page);
  await fillComparison(page, setup, false);
  let sentRequestId = "";
  const collection = /\/api\/skill-calibrations(?:\?.*)?$/;
  await page.route(collection, async route => {
    if (route.request().method() === "POST") { sentRequestId = route.request().postDataJSON().request_id; await route.fetch(); await route.abort("failed"); }
    else await route.abort("failed");
  });
  await page.getByRole("button", { name: "生成修改草稿", exact: true }).click();
  await expect(page.getByRole("button", { name: "核对并恢复这次提交", exact: true })).toBeVisible();
  await page.unroute(collection);
  await page.reload();
  await expect(page).toHaveURL(/\/skills\?extraction=.*calibration=/);
  const result = await (await page.request.get(`/api/skill-calibrations?series_id=${setup.series_id}`)).json();
  expect(result.items).toHaveLength(1);
  expect(result.items[0].request_id).toBe(sentRequestId);
});
