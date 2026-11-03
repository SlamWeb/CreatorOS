import { expect, test } from "@playwright/test";
import { ensureBuiltinImageSkill } from "./fixtures/image-default";

test("product actions retain form, conflict, navigation and download behavior", async ({ page }, testInfo) => {
  // Bound Skills + a controlled producer in isolated SQLite, never a paid model.
  const setup = await page.request.post("/test/calibration", { data: {} });
  expect(setup.ok()).toBeTruthy();
  const { series_id, creator_id } = await setup.json();
  const skill = await ensureBuiltinImageSkill(page.request, creator_id);
  await page.goto(`/?series=${series_id}`);
  await page.getByRole("button", { name: "添加选题", exact: true }).click();
  await page.getByLabel("新选题标题").fill("UI 控件迁移验收");
  await page.getByRole("button", { name: "添加", exact: true }).click();
  await expect(page.getByRole("heading", { name: "UI 控件迁移验收", exact: true })).toBeVisible();
  const topics = await (await page.request.get(`/api/series/${series_id}/topics`)).json();
  const topic = topics.items.find((item: { title: string }) => item.title === "UI 控件迁移验收");
  const creation = await page.request.post("/api/runs", { data: { topic_id: topic.id, request_id: crypto.randomUUID(),
    skill_overrides: { skill_id: skill.id, expected_digests: { [skill.id]: skill.digest } },
  } });
  expect(creation.ok(), await creation.text()).toBeTruthy();
  const run = await creation.json();
  const start = await page.request.post(`/api/runs/${run.id}/execute`, { data: { expected_version: run.version } });
  expect(start.ok()).toBeTruthy();
  // An unrelated Skill request must not delay a visible conflict. Leave it
  // pending until after the conflict is shown, as can happen on a large library.
  let releaseSkills!: () => void, signalSkills!: () => void;
  const skillsBlocked = new Promise<void>(resolve => { signalSkills = resolve; });
  const skillsGate = new Promise<void>(resolve => { releaseSkills = resolve; });
  const holdSkills = async (route: import("@playwright/test").Route) => {
    signalSkills();
    await skillsGate;
    try { await route.continue(); } catch (error) {
      if (!String(error).includes("Route is already handled")) throw error;
    }
  };
  await page.route("**/api/producer-skills**", holdSkills);
  await page.goto(`/runs/${run.id}`);
  await skillsBlocked;
  const runUrl = page.url();
  await expect(page.getByRole("button", { name: "批准第 1 版" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("button", { name: "批准第 1 版" })).toHaveCSS("background-color", "rgb(0, 113, 227)");
  await page.screenshot({ path: testInfo.outputPath("product-desktop.png"), fullPage: true });

  await page.getByRole("button", { name: "提出返工", exact: true }).click();
  const instructions = page.getByLabel("告诉生产者要改哪里");
  await expect(instructions).toBeFocused();
  await expect(page.getByRole("button", { name: "保存返工要求" })).toBeDisabled();
  await instructions.fill("保留第一张，只调整第二张。");
  await page.route("**/api/runs/*/revisions", route => route.fulfill({
    status: 409, json: { error: { code: "version_conflict", message: "受控冲突：页面版本已过期" } },
  }), { times: 1 });
  try {
    await page.getByRole("button", { name: "保存返工要求" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "内容已变化" })).toBeVisible();
    await expect(instructions).toHaveValue("保留第一张，只调整第二张。");
    await expect(page.getByRole("button", { name: "批准第 1 版" })).toBeDisabled();
  } finally {
    releaseSkills();
    await page.unroute("**/api/producer-skills**", holdSkills);
  }
  await page.getByRole("button", { name: "重新检查", exact: true }).click();
  await expect(page.getByRole("button", { name: "批准第 1 版" })).toBeEnabled();
  await page.getByRole("button", { name: "收起", exact: true }).click();

  await page.getByRole("button", { name: "更多", exact: true }).click();
  await expect(page.getByRole("button", { name: "删除该条内容", exact: true })).toHaveCSS("color", "rgb(196, 66, 59)");
  await page.getByRole("button", { name: "删除该条内容", exact: true }).click();
  const confirmation = page.getByRole("group", { name: "确认删除内容" });
  await expect(confirmation).toBeVisible();
  await expect(confirmation.getByRole("button", { name: "确认删除", exact: true })).toHaveCSS("color", "rgb(196, 66, 59)");
  await confirmation.getByRole("button", { name: "取消", exact: true }).click();
  await expect(confirmation).toHaveCount(0);

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("button", { name: "提出返工", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({ path: testInfo.outputPath("product-mobile.png"), fullPage: true });
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载全部（3）", exact: true }).click();
  expect((await download).suggestedFilename()).toMatch(/\.zip$/);
  await page.reload();
  await expect(page.getByRole("button", { name: "批准第 1 版" })).toBeVisible();
  await page.getByRole("link", { name: "返回栏目", exact: false }).click();
  await expect(page.getByRole("region", { name: "选题库" })).toBeVisible();
  expect(new URL(page.url()).searchParams.get("series")).toBe(series_id);
  await page.goto(runUrl);
  await expect(page.getByRole("button", { name: "批准第 1 版" })).toBeVisible();
  const failedRead = async (route: import("@playwright/test").Route) => route.fulfill({
    status: 503, json: { error: { message: "受控服务暂不可用" } },
  });
  await page.route(`**/api/runs/${run.id}`, failedRead);
  await page.reload();
  await expect(page.getByRole("button", { name: "重新连接", exact: true })).toBeVisible();
  await page.unroute(`**/api/runs/${run.id}`, failedRead);
  await page.getByRole("button", { name: "重新连接", exact: true }).click();
  await expect(page.getByRole("button", { name: "批准第 1 版" })).toBeVisible();
  await page.getByRole("button", { name: "提出返工", exact: true }).click();
  await instructions.fill("保留第一张，只调整第二张。");
  await page.getByRole("button", { name: "保存返工要求" }).click();
  await expect(page.getByRole("button", { name: "开始生产", exact: true })).toBeVisible();
  const saved = await (await page.request.get(`/api/runs/${run.id}`)).json();
  expect(saved.revisions).toHaveLength(2);
  expect(saved.revisions[0].cards).toHaveLength(3);
  expect(saved.revisions[1].instruction).toBe("保留第一张，只调整第二张。");
  expect(saved.revisions[1].attempts).toHaveLength(0);
  await page.getByRole("button", { name: "开始生产", exact: true }).click();
  await page.getByRole("button", { name: "批准第 2 版" }).click();
  await expect(page.getByText("已批准 · 尚未发布", { exact: true })).toBeVisible();
  expect((await (await page.request.get(`/api/runs/${run.id}`)).json()).status).toBe("approved");
  await page.reload();
  await expect(page.getByText("已批准 · 尚未发布", { exact: true })).toBeVisible();
});

async function readyReview(page: import("@playwright/test").Page) {
  const setup = await page.request.post("/test/calibration", { data: {} });
  expect(setup.ok()).toBeTruthy();
  const { series_id, creator_id } = await setup.json();
  const skill = await ensureBuiltinImageSkill(page.request, creator_id);
  const title = `审阅恢复-${crypto.randomUUID()}`;
  const topic = await page.request.post(`/api/series/${series_id}/queue`, { data: {
    topics: [{ title }], request_id: crypto.randomUUID().replaceAll("-", ""),
  } });
  expect(topic.ok(), await topic.text()).toBeTruthy();
  const topicId = (await topic.json()).topic_ids[0];
  const runResponse = await page.request.post("/api/runs", { data: { topic_id: topicId,
    request_id: crypto.randomUUID(), skill_overrides: { skill_id: skill.id, expected_digests: { [skill.id]: skill.digest } } } });
  expect(runResponse.ok(), await runResponse.text()).toBeTruthy();
  const run = await runResponse.json();
  expect((await page.request.post(`/api/runs/${run.id}/execute`, { data: { expected_version: run.version } })).ok()).toBeTruthy();
  await page.goto(`/runs/${run.id}`);
  await expect(page.getByRole("button", { name: "批准第 1 版" })).toBeVisible({ timeout: 20_000 });
  return run.id as string;
}

for (const mode of ["lost-approve", "bad-json-revise"] as const) {
  test(`${mode}: committed review stays locked across reload until read-only verification`, async ({ page }) => {
    const runId = await readyReview(page);
    const action = mode === "lost-approve" ? "approve" : "revisions";
    let writes = 0;
    await page.route(`**/api/runs/${runId}/${action}`, async route => {
      writes++;
      const actual = await route.fetch();
      expect(actual.ok(), await actual.text()).toBeTruthy();
      if (mode === "lost-approve") await route.abort("failed");
      else await route.fulfill({ status: 200, contentType: "application/json", body: "{" });
    });
    if (mode === "lost-approve") await page.getByRole("button", { name: "批准第 1 版" }).evaluate(button => {
      (button as HTMLButtonElement).click(); (button as HTMLButtonElement).click();
    });
    else {
      await page.getByRole("button", { name: "提出返工", exact: true }).click();
      await page.getByLabel("告诉生产者要改哪里").fill("只修改第二页的例子");
      await page.getByRole("button", { name: "保存返工要求" }).click();
    }
    await expect(page.getByRole("alert").filter({ hasText: "上次操作结果待核对" })).toBeVisible();
    expect(writes).toBe(1);
    await page.reload();
    const pending = page.getByRole("alert").filter({ hasText: "上次操作结果待核对" });
    await expect(pending).toBeVisible();
    if (mode === "bad-json-revise") {
      await expect(page.getByLabel("告诉生产者要改哪里")).toHaveValue("只修改第二页的例子");
      await expect(page.getByRole("button", { name: "保存返工要求" })).toBeDisabled();
    }
    await pending.getByRole("button", { name: "重新检查" }).click();
    await expect(pending).toHaveCount(0);
    const actual = await (await page.request.get(`/api/runs/${runId}`)).json();
    if (mode === "lost-approve") {
      expect(actual.status).toBe("approved");
      await expect(page.getByText("已批准 · 尚未发布", { exact: true })).toBeVisible();
    } else {
      expect(actual.revisions).toHaveLength(2);
      expect(actual.revisions[1].instruction).toBe("只修改第二页的例子");
      expect(actual.revisions[1].attempts).toHaveLength(0);
      await expect(page.getByLabel("告诉生产者要改哪里")).toHaveCount(0);
    }
    expect(writes).toBe(1);
  });
}

test("review conflict keeps input and lock when verification GET fails", async ({ page }) => {
  const runId = await readyReview(page);
  let writes = 0;
  await page.route(`**/api/runs/${runId}/revisions`, route => {
    writes++; return route.fulfill({ status: 409, json: { error: { code: "version_conflict", message: "controlled conflict" } } });
  });
  await page.getByRole("button", { name: "提出返工", exact: true }).click();
  const instruction = page.getByLabel("告诉生产者要改哪里");
  await instruction.fill("保留此草稿");
  await page.getByRole("button", { name: "保存返工要求" }).click();
  const pending = page.getByRole("alert").filter({ hasText: "内容已变化" });
  await expect(pending).toBeVisible();
  await page.route(`**/api/runs/${runId}`, route => route.fulfill({ status: 503, body: "unavailable" }), { times: 1 });
  await pending.getByRole("button", { name: "重新检查" }).click();
  await expect(pending).toContainText("读取或保存核对状态失败");
  await expect(instruction).toHaveValue("保留此草稿");
  await expect(page.getByRole("button", { name: "保存返工要求" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "批准第 1 版" })).toBeDisabled();
  await pending.getByRole("button", { name: "重新检查" }).click();
  await expect(pending).toHaveCount(0);
  await expect(instruction).toHaveValue("保留此草稿");
  await expect(page.getByRole("button", { name: "保存返工要求" })).toBeEnabled();
  expect(writes).toBe(1);
});

for (const responseStatus of [503, 409]) test(`unknown ${responseStatus} review with unchanged Run remains locked after read and reload`, async ({ page }) => {
  const runId = await readyReview(page);
  let writes = 0;
  const revise = responseStatus === 503;
  await page.route(`**/api/runs/${runId}/${revise ? "revisions" : "approve"}`, route => {
    writes++; return route.fulfill({ status: responseStatus, body: "unknown" });
  });
  if (revise) {
    await page.getByRole("button", { name: "提出返工", exact: true }).click();
    await page.getByLabel("告诉生产者要改哪里").fill("保留待核对草稿");
    await page.getByRole("button", { name: "保存返工要求" }).click();
  } else await page.getByRole("button", { name: "批准第 1 版" }).click();
  let pending = page.getByRole("alert").filter({ hasText: "上次操作结果待核对" });
  await expect(pending).toBeVisible();
  await pending.getByRole("button", { name: "重新检查" }).click();
  await expect(pending).toContainText("当前状态尚未变化");
  await expect(page.getByRole("button", { name: "批准第 1 版" })).toBeDisabled();
  await page.reload();
  pending = page.getByRole("alert").filter({ hasText: "上次操作结果待核对" });
  await expect(pending).toBeVisible();
  await expect(page.getByRole("button", { name: "批准第 1 版" })).toBeDisabled();
  await expect(pending.getByRole("button", { name: "已核实，解除锁定" })).toHaveCount(0);
  if (revise) await expect(page.getByLabel("告诉生产者要改哪里")).toHaveValue("保留待核对草稿");
  await pending.getByRole("button", { name: "重新检查" }).click();
  await expect(pending.getByRole("button", { name: "已核实，解除锁定" })).toBeVisible();
  page.once("dialog", dialog => dialog.dismiss());
  await pending.getByRole("button", { name: "已核实，解除锁定" }).click();
  await expect(pending).toBeVisible();
  page.once("dialog", dialog => dialog.accept());
  await pending.getByRole("button", { name: "已核实，解除锁定" }).click();
  await expect(pending).toHaveCount(0);
  await expect(page.getByRole("button", { name: "批准第 1 版" })).toBeEnabled();
  if (revise) {
    await expect(page.getByLabel("告诉生产者要改哪里")).toHaveValue("保留待核对草稿");
    await expect(page.getByRole("button", { name: "保存返工要求" })).toBeEnabled();
  }
  expect(writes).toBe(1);
});

test("explicit review validation rejection preserves input without locking", async ({ page }) => {
  const runId = await readyReview(page);
  let writes = 0;
  await page.route(`**/api/runs/${runId}/revisions`, route => {
    writes++; return route.fulfill({ status: 422, json: { error: { message: "明确拒绝" } } });
  });
  await page.getByRole("button", { name: "提出返工", exact: true }).click();
  await page.getByLabel("告诉生产者要改哪里").fill("保留输入");
  await page.getByRole("button", { name: "保存返工要求" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "明确拒绝" })).toBeVisible();
  await expect(page.getByLabel("告诉生产者要改哪里")).toHaveValue("保留输入");
  await expect(page.getByRole("button", { name: "保存返工要求" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "批准第 1 版" })).toBeEnabled();
  expect(writes).toBe(1);
});

test("review is not submitted if the pending marker cannot be persisted", async ({ page }) => {
  const runId = await readyReview(page);
  let writes = 0;
  page.on("request", request => {
    if (request.method() === "POST" && new URL(request.url()).pathname === `/api/runs/${runId}/approve`) writes++;
  });
  await page.evaluate(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (key.startsWith("creatoros.run-review-pending.v1:")) throw new Error("storage blocked");
      return original.call(this, key, value);
    };
  });
  await page.getByRole("button", { name: "批准第 1 版" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "无法保存待核对状态，本次没有提交" })).toBeVisible();
  expect(writes).toBe(0);
  expect((await (await page.request.get(`/api/runs/${runId}`)).json()).status).toBe("awaiting_approval");
});
