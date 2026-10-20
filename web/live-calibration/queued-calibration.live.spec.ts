import { expect, test } from "@playwright/test";

// Real browser + real Codex. Only source account/content are isolated fixtures.
// No model stubs, no routes intercepted, no automatic retry of paid requests.
test("real queued extraction, research, calibration edit, clean trial and explicit save", async ({ page, context }, info) => {
  test.skip(process.env.CREATOROS_LIVE_CALIBRATION !== "1", "Explicit paid opt-in required");
  const evidence = async () => {
    const response = await page.request.get("/api/calibration-probe/evidence");
    expect(response.ok()).toBeTruthy();
    return response.json();
  };
  const before = await evidence();
  const setup = before.setup;
  expect(setup.live).toBe(true);
  const second = await context.newPage();
  const research = await context.newPage();
  let calibrationId: string, secondJob: string, firstResearch: string, secondResearch: string;
  if (process.env.CREATOROS_LIVE_RESUME === "1") {
    // Read existing tasks after a browser interruption; never resubmit paid work.
    expect(before.calibrations).toHaveLength(1);
    calibrationId = before.calibrations[0].id;
    const jobs = await (await page.request.get("/api/skill-extractions")).json();
    const extracts = jobs.items.filter((job: { task_kind?: string }) => job.task_kind !== "calibrate");
    expect(extracts).toHaveLength(1);
    secondJob = extracts[0].id;
    const batches = await (await page.request.get(`/api/series/${setup.series_id}/topic-research`)).json();
    expect(batches.items).toHaveLength(2);
    [firstResearch, secondResearch] = batches.items.map((batch: { id: string }) => batch.id);
    await page.goto(`/skills?extraction=${before.calibrations[0].job_id}&calibration=${calibrationId}`);
    await second.goto(`/skills?extraction=${secondJob}`);
    await research.goto(`/series/${setup.series_id}?research=${secondResearch}`);
  } else {
  await page.goto(`/runs/${setup.run_id}?revision=${setup.revision_id}`);
  await page.getByRole("link", { name: "改进 Skill", exact: true }).click();
  await page.getByLabel("要改进的 Skill", { exact: true }).selectOption(setup.skill_id);
  await page.getByLabel("产物具体评价", { exact: true }).fill("保留具体道具和行动，用开头的蛋糕在结尾送给朋友，不要只讲道理。");
  await page.getByRole("button", { name: "加入对照", exact: true }).click();
  await page.getByLabel("校准其他要求", { exact: true }).fill("给出简短可复用故事方法，参考实际图片；只改Mind，不改画风。");
  await page.getByRole("button", { name: "生成修改草稿", exact: true }).click();
  await expect(page).toHaveURL(/\/skills\?extraction=.*calibration=/);
  calibrationId = new URL(page.url()).searchParams.get("calibration")!;

  await second.goto("/skills");
  await second.getByRole("button", { name: "从作品提炼", exact: true }).click();
  const newWork = second.getByRole("button", { name: /新作品/ });
  if (await newWork.getAttribute("aria-expanded") === "false") await newWork.click();
  await second.getByLabel("参考文案", { exact: true }).fill("一只小狗做好蛋糕，看到别人精致的蛋糕后想把自己的藏起来。朋友找过来，小狗把朴素蛋糕递给朋友，两人一起吃。");
  await second.getByLabel("提炼方式", { exact: true }).selectOption("mind");
  await second.getByRole("button", { name: "开始提炼", exact: true }).click();
  await expect(second).toHaveURL(/extraction=/);
  await expect(second.getByLabel("提炼任务详情").getByText("排队中", { exact: true })).toBeVisible();
  secondJob = new URL(second.url()).searchParams.get("extraction")!;
  await second.reload();
  await expect(second.getByLabel("提炼任务详情").getByText("排队中", { exact: true })).toBeVisible();
  await second.screenshot({ path: info.outputPath("queued-extraction.png"), fullPage: true });

  await research.goto(`/series/${setup.series_id}`);
  await research.getByRole("button", { name: "调研选题", exact: true }).first().click();
  await research.getByLabel("候选数量", { exact: true }).fill("1");
  await research.getByLabel("本次偏好", { exact: true }).fill("调研一个适合道具首尾回扣的友情日常漫画选题，用真实可访问来源支持，避免已有蛋糕情节。");
  await research.getByRole("button", { name: "开始联网调研", exact: true }).click();
  await expect(research).toHaveURL(/research=/);
  firstResearch = new URL(research.url()).searchParams.get("research")!;
  await research.getByRole("button", { name: "重新调研", exact: true }).click();
  await research.getByLabel("候选数量", { exact: true }).fill("1");
  await research.getByLabel("本次偏好", { exact: true }).fill("调研一个关于邻里互助的具体日常漫画选题，有真实可访问来源，不要蛋糕、友情比较情节。");
  await research.getByRole("button", { name: "开始联网调研", exact: true }).click();
  await expect.poll(() => new URL(research.url()).searchParams.get("research")).not.toBe(firstResearch);
  await expect(research.getByText("已排队，轮到后自动开始。可以继续提交其他调研。", { exact: true })).toBeVisible();
  secondResearch = new URL(research.url()).searchParams.get("research")!;
  expect(secondResearch).not.toBe(firstResearch);
  await research.reload();
  await expect(research.getByText("已排队，轮到后自动开始。可以继续提交其他调研。", { exact: true })).toBeVisible();
  await research.screenshot({ path: info.outputPath("queued-research.png"), fullPage: true });
  }

  await expect(page.getByRole("button", { name: "编辑", exact: true })).toBeEnabled({ timeout: 900_000 });
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  const editor = page.getByLabel("编辑 SKILL.md", { exact: true });
  await editor.fill(`${await editor.inputValue()}\n\n本次试用检查：开头的蛋糕应在结尾出现，以送蛋糕的动作收尾，不增加说教字幕。\n`);
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.getByText("有未保存的草稿修改", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: /修改差异与依据/ }).click();
  await expect(page.getByLabel("Skill 修改差异")).toContainText("本次试用检查");
  await page.getByLabel("试产选题", { exact: true }).fill("只制作一张四格暖色漫画：小狗做好一个朴素蛋糕，看到别人的漂亮蛋糕后想藏起来；朋友过来，小狗把开头的蛋糕递给朋友，两人一起吃。");
  await page.getByRole("button", { name: "主动开始试产", exact: true }).click();
  await expect(page.locator(".extraction-trial").last().getByText("试产完成", { exact: true })).toBeVisible({ timeout: 900_000 });
  const trialEvidence = await evidence();
  expect(trialEvidence.protected_records).toEqual(before.protected_records);
  expect(trialEvidence.target_files).toEqual(before.target_files);
  expect(trialEvidence.companion_files).toEqual(before.companion_files);
  expect(trialEvidence.artifact_files).toEqual(before.artifact_files);
  const calibration = trialEvidence.calibrations.find((item: { id: string }) => item.id === calibrationId);
  expect(calibration.trials).toHaveLength(1);
  expect(calibration.trials[0].thread_id).not.toBe(calibration.thread_id);
  await page.screenshot({ path: info.outputPath("real-trial-desktop.png"), fullPage: true });
  await page.getByRole("button", { name: "确认更新当前 Skill", exact: true }).click();
  await page.getByRole("button", { name: "试用满意，确认保存", exact: true }).click();
  await expect(page.getByText("已确认更新", { exact: true })).toBeVisible({ timeout: 30000 });
  await expect.poll(async () => (await second.request.get(`/api/skill-extractions/${secondJob}`)).json().then(job => job.status),
    { timeout: 900_000, intervals: [3000] }).toBe("ready");
  for (const id of [firstResearch, secondResearch]) {
    await expect.poll(async () => (await research.request.get(`/api/topic-research/${id}`)).json().then(batch => batch.status),
      { timeout: 900_000, intervals: [3000] }).toBe("ready");
  }
  await second.reload();
  await expect(second.getByRole("button", { name: "编辑", exact: true })).toBeEnabled();
  await research.reload();
  await expect(research.getByText("1 个候选", { exact: true })).toBeVisible();
  const after = await evidence();
  expect(after.protected_records).toEqual(before.protected_records);
  expect(after.target_files).not.toEqual(before.target_files);
  expect(after.companion_files).toEqual(before.companion_files);
  expect(after.artifact_files).toEqual(before.artifact_files);
  await info.attach("real-chain-evidence.json", { body: JSON.stringify({ before, after, firstResearch, secondResearch, secondJob }, null, 2), contentType: "application/json" });
});

test("real DeepSeek account chat reads completed research without starting another task", async ({ page }, info) => {
  test.skip(process.env.CREATOROS_LIVE_CALIBRATION !== "1", "Explicit paid opt-in required");
  const before = await (await page.request.get("/api/calibration-probe/evidence")).json();
  const setup = before.setup;
  const batches = await (await page.request.get(`/api/series/${setup.series_id}/topic-research`)).json();
  expect(batches.items).toHaveLength(2);
  expect(batches.items.every((item: { status: string }) => item.status === "ready")).toBe(true);
  await page.goto(`/agent?creator=${setup.creator_id}`);
  await page.getByRole("textbox", { name: "给 Agent 的消息" }).fill("请查询这个账号「[隔离验证] 故事校准」栏目的两批已完成调研，告诉我各自的候选标题，简短回复。不要重新调研，不要入队或生产，不要修改Skill。");
  const posted = page.waitForResponse(response => response.request().method() === "POST" && /\/turns$/.test(new URL(response.url()).pathname));
  await page.getByRole("button", { name: "发送 ↑", exact: true }).click();
  expect((await posted).status()).toBe(202);
  const sid = new URL(page.url()).searchParams.get("chat")!;
  expect(sid).toBeTruthy();
  let doc: any;
  await expect.poll(async () => {
    doc = await (await page.request.get(`/api/agent/sessions/${sid}`)).json();
    return doc.status;
  }, { timeout: 180_000, intervals: [2000] }).toBe("idle");
  expect(doc.error).toBeNull();
  const finalEntries = doc.entries.filter((entry: { kind: string }) => entry.kind === "assistant");
  expect(finalEntries.length).toBeGreaterThan(0);
  for (const batch of batches.items) {
    const detail = await (await page.request.get(`/api/topic-research/${batch.id}`)).json();
    for (const candidate of detail.candidates) {
      await expect(page.getByLabel("对话记录")).toContainText(candidate.title);
    }
  }
  const after = await (await page.request.get("/api/calibration-probe/evidence")).json();
  expect(after.protected_records).toEqual(before.protected_records);
  expect(after.target_files).toEqual(before.target_files);
  const afterBatches = await (await page.request.get(`/api/series/${setup.series_id}/topic-research`)).json();
  expect(afterBatches.items.map((item: { id: string }) => item.id)).toEqual(batches.items.map((item: { id: string }) => item.id));
  await info.attach("deepseek-chat.json", { body: JSON.stringify(doc, null, 2), contentType: "application/json" });
  await page.screenshot({ path: info.outputPath("deepseek-research-read.png"), fullPage: true });
});
