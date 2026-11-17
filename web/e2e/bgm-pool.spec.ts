import { expect, test, type APIRequestContext } from "@playwright/test";

const creatorId = "creator-f2304a25ec244d8484d8";
const root = `/api/creators/${creatorId}/materials`;
function wav(name = "受控音乐.wav", frequency = 440) {
  const samples = 16000;
  const data = Buffer.alloc(44 + samples * 2);
  data.write("RIFF", 0); data.writeUInt32LE(data.length - 8, 4); data.write("WAVEfmt ", 8);
  data.writeUInt32LE(16, 16); data.writeUInt16LE(1, 20); data.writeUInt16LE(1, 22); data.writeUInt32LE(8000, 24);
  data.writeUInt32LE(16000, 28); data.writeUInt16LE(2, 32); data.writeUInt16LE(16, 34); data.write("data", 36); data.writeUInt32LE(samples * 2, 40);
  for (let index = 0; index < samples; index++) data.writeInt16LE(Math.round(2000 * Math.sin(index * Math.PI * 2 * frequency / 8000)), 44 + index * 2);
  return { name, mimeType: "audio/wav", buffer: data };
}

async function post(request: APIRequestContext, path: string, data: object) {
  const response = await request.post(path, { data });
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}

test("account chat still retains and retries the same scoped audio attachment", async ({ page, request }) => {
  expect((await request.post("/test/bgm-account", { data: {} })).ok()).toBeTruthy();
  const uploads: unknown[] = [];
  // Controlled analyzer: retain real upload/SQLite/HTTP, but do not run CPU music models in this UI test.
  await page.route(`**${root}`, async route => {
    if (route.request().method() !== "POST") return route.continue();
    const payload = route.request().postDataJSON(); uploads.push(payload.analyze);
    const response = await route.fetch({ postData: JSON.stringify({ ...payload, analyze: false }) });
    await route.fulfill({ response });
  });
  const submitted: { material_ids?: string[]; request_id?: string; text?: string }[] = [];
  await page.route("**/api/agent/sessions/*/turns", async route => {
    submitted.push(route.request().postDataJSON());
    await route.fulfill({ status: 503, json: { error: { message: "受控请求待核对" } } });
  });
  await page.goto(`/?creator=${creatorId}`);
  await page.getByRole("button", { name: "打开账号对话" }).click();
  const panel = page.locator(".account-chat-panel");
  await panel.getByLabel("选择音频文件").setInputFiles(wav());
  await expect(panel.getByLabel("待发送音频")).toContainText("受控音乐.wav");
  const data = await (await request.get(root)).json();
  expect(data.items).toHaveLength(1);
  await page.reload();
  await expect(panel.getByLabel("待发送音频")).toContainText("受控音乐.wav");
  await expect(panel.getByRole("textbox", { name: "给 Agent 的消息" })).toHaveValue("");
  await panel.getByRole("button", { name: "发送 ↑" }).click();
  await expect.poll(() => submitted.length).toBe(1);
  expect(submitted[0].text).toBe("");
  expect(submitted[0].material_ids).toEqual([data.items[0].id]);
  await expect(panel.getByText("上次提交待核对")).toBeVisible();
  await page.reload();
  await expect(panel.getByText("上次提交待核对")).toBeVisible();
  await expect(panel.getByLabel("待发送音频")).toContainText("受控音乐.wav");
  await panel.getByRole("button", { name: "核对并恢复上次提交" }).click();
  await expect.poll(() => submitted.length).toBe(2);
  expect(submitted[1].request_id).toBe(submitted[0].request_id);
  expect(submitted[1].material_ids).toEqual(submitted[0].material_ids);
  expect(uploads).toEqual([true]);
});

test("global BGM works across accounts, archives references and restores the same audio", async ({ page, request }, info) => {
  const gate = await post(request, "/api/creators", { display_name: `歌单甲-${crypto.randomUUID().slice(0, 8)}` });
  const other = await post(request, "/api/creators", { display_name: `歌单乙-${crypto.randomUUID().slice(0, 8)}` });
  const first = await post(request, `/api/creators/${gate.id}/series`, { name: "测试歌单甲" });
  const second = await post(request, `/api/creators/${other.id}/series`, { name: "测试歌单乙" });
  const name = "跨账号试听音乐.wav";
  const route = "/api/bgm";
  await page.goto("/workspace");
  await page.getByRole("link", { name: "BGM 池", exact: true }).click();
  await page.getByLabel("上传音频", { exact: true }).setInputFiles(wav(name, 523));
  const filter = page.getByRole("searchbox", { name: "搜索音乐" });
  await filter.fill("跨账号试听");
  const row = page.locator(".bgm-row").filter({ has: page.getByRole("heading", { name, exact: true }) });
  await expect(row).toBeVisible({ timeout: 15_000 });
  await expect(row).toContainText("文件上传");
  await expect(row).toContainText("0:02");
  const tracks = await request.get(`${route}?q=跨账号试听`).then(response => response.json());
  expect(tracks.items).toHaveLength(1);
  const track = tracks.items[0];
  const audio = row.getByLabel(`试听 ${name}`, { exact: true });
  await audio.evaluate((element: HTMLAudioElement) => element.play());
  await expect.poll(() => audio.evaluate((element: HTMLAudioElement) => element.currentTime)).toBeGreaterThan(0);
  await audio.evaluate((element: HTMLAudioElement) => element.pause());
  await page.screenshot({ path: info.outputPath("bgm-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: info.outputPath("bgm-mobile.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.setViewportSize({ width: 1440, height: 900 });

  for (const item of [{ account: gate, series: first }, { account: other, series: second }]) {
    await page.goto(`/workspace?creator=${item.account.id}&series=${item.series.id}`);
    await page.getByRole("button", { name: "歌单", exact: true }).click();
    const playlist = page.getByRole("region", { name: "栏目歌单" });
    await playlist.getByLabel("从 BGM 池添加").selectOption(track.id);
    await playlist.getByRole("button", { name: "添加到歌单" }).click();
    await expect(playlist.getByRole("heading", { name, exact: true })).toBeVisible();
    const recorded = await request.get(`/api/series/${item.series.id}/bgm`).then(response => response.json());
    expect(recorded.items.map((item: { id: string }) => item.id)).toEqual([track.id]);
  }
  await page.screenshot({ path: info.outputPath("series-playlist-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: info.outputPath("series-playlist-mobile.png"), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole("link", { name: "打开 BGM 池", exact: true }).click();
  await row.getByRole("button", { name: "归档", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "归档音乐" });
  await expect(dialog.getByRole("link", { name: `${first.name}歌单` })).toBeVisible();
  await expect(dialog.getByRole("link", { name: `${second.name}歌单` })).toBeVisible();
  await dialog.getByRole("button", { name: "确认归档" }).click();
  await expect(row).toHaveCount(0);
  await page.getByRole("button", { name: "已归档", exact: true }).click();
  await expect(row).toBeVisible();
  await page.goto(`/workspace?creator=${other.id}&series=${second.id}&view=playlist`);
  await expect(page.getByRole("region", { name: "栏目歌单" })).toContainText("已归档 · 暂不可用于新制作");
  await page.getByRole("link", { name: "打开 BGM 池", exact: true }).click();
  await page.getByRole("button", { name: "已归档", exact: true }).click();
  await row.getByRole("button", { name: "恢复", exact: true }).click();
  await expect(row).toHaveCount(0);
  await page.getByRole("button", { name: "在库", exact: true }).click();
  await expect(row).toBeVisible();
  await page.goto(`/workspace?creator=${other.id}&series=${second.id}&view=playlist`);
  await page.getByRole("button", { name: `移出歌单 ${name}`, exact: true }).click();
  await expect(page.getByRole("region", { name: "栏目歌单" }).getByRole("heading", { name, exact: true })).toHaveCount(0);
  expect(await request.get(`${route}/${track.id}`).then(response => response.json())).toMatchObject({ id: track.id, archived: false, reference_count: 1 });
});

test("failed global link import can retry the same persisted job", async ({ page }) => {
  const source = "分享文字 https://example.com/video/123";
  const job = { id: "controlled-failure", status: "failed", source_url: source, message: "受控导入失败" };
  let submitted = false;
  let retried = false;
  await page.route("**/api/bgm/imports", async route => {
    if (route.request().method() === "POST") {
      expect(route.request().postDataJSON().source_url).toBe(source);
      submitted = true;
      return route.fulfill({ status: 202, json: job });
    }
    await route.fulfill({ json: { items: submitted ? [{ ...job, status: retried ? "completed" : "failed" }] : [] } });
  });
  await page.route(`**/api/bgm/imports/${job.id}/retry`, async route => {
    retried = true;
    await route.fulfill({ status: 202, json: { ...job, status: "completed" } });
  });
  await page.goto("/bgm");
  const input = page.getByRole("textbox", { name: "添加分享链接" });
  await input.fill(source);
  await page.getByRole("button", { name: "导入", exact: true }).click();
  await expect(page.getByRole("region", { name: "待处理导入" }).getByRole("alert")).toContainText("受控导入失败");
  await expect(input).toHaveValue(source);
  await page.reload();
  await page.getByRole("button", { name: "重试导入", exact: true }).click();
  await expect(page.getByText("已完成的导入", { exact: true })).toBeVisible();
  expect(retried).toBe(true);
});

test("full-track analysis shows rhythm and segments and supports explicit reanalysis", async ({ page }) => {
  const id = "controlled-track";
  let state = "completed";
  await page.route(url => url.pathname === "/api/bgm", route => route.fulfill({ json: { items: [{
    id, name: "受控整首音乐", duration_seconds: 132, kind: "audio", source_type: "upload", url: "/controlled.wav",
    archived: false, analysis_available: true, analysis_status: state, reference_count: 0,
  }] } }));
  await page.route(`**/api/bgm/${id}/analysis`, route => route.fulfill({ json: {
    status: state, analysis_available: true, overview: { bpm: 120 }, beat_count: 264, onset_count: 312,
    sections: [{ label: "开场", start_seconds: 0, end_seconds: 12 }, { label: "主体", start_seconds: 12, end_seconds: 132 }],
  } }));
  await page.route(`**/api/bgm/${id}/analysis/run`, route => {
    expect(route.request().postDataJSON()).toEqual({ force: true });
    state = "queued";
    return route.fulfill({ status: 202, json: { status: state, analysis_available: true } });
  });
  await page.goto("/bgm");
  await page.getByRole("button", { name: "查看分析", exact: true }).click();
  await expect(page.getByText("速度 120 BPM")).toBeVisible();
  await expect(page.getByRole("list", { name: "音乐段落" }).getByRole("listitem").last()).toContainText("2:12 主体");
  await page.getByRole("button", { name: "重新分析", exact: true }).click();
  await expect(page.getByRole("button", { name: "等待分析", exact: true })).toBeDisabled();
  state = "completed";
  await expect(page.getByRole("button", { name: "重新分析", exact: true })).toBeEnabled({ timeout: 10_000 });
});
