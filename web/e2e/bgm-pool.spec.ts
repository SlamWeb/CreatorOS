import { expect, test } from "@playwright/test";

const creatorId = "creator-f2304a25ec244d8484d8";
const root = `/api/creators/${creatorId}/materials`;
function wav() {
  const data = Buffer.alloc(44 + 16000);
  data.write("RIFF", 0); data.writeUInt32LE(data.length - 8, 4); data.write("WAVEfmt ", 8);
  data.writeUInt32LE(16, 16); data.writeUInt16LE(1, 20); data.writeUInt16LE(1, 22); data.writeUInt32LE(8000, 24);
  data.writeUInt32LE(16000, 28); data.writeUInt16LE(2, 32); data.writeUInt16LE(16, 34); data.write("data", 36); data.writeUInt32LE(16000, 40);
  return { name: "受控音乐.wav", mimeType: "audio/wav", buffer: data };
}

test("BGM account can upload audio, retain it in the pool and attach its ID to a chat turn", async ({ page, request }, info) => {
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
  await page.getByRole("button", { name: "BGM", exact: true }).click();
  await expect(page.getByRole("region", { name: "BGM 池" })).toBeVisible();
  await page.locator('.bgm-upload input[type="file"]').setInputFiles(wav());
  await expect(page.locator(".bgm-row")).toContainText("受控音乐.wav");
  const data = await (await request.get(root)).json();
  expect(data.items).toHaveLength(1);
  expect(uploads).toEqual([true]);
  await page.reload();
  await expect(page.locator(".bgm-row")).toContainText("受控音乐.wav");
  await page.screenshot({ path: info.outputPath("bgm-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({ path: info.outputPath("bgm-mobile.png"), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole("button", { name: "打开账号对话" }).click();
  const panel = page.locator(".account-chat-panel");
  await panel.getByLabel("选择音频文件").setInputFiles(wav());
  await expect(panel.getByLabel("待发送音频")).toContainText("受控音乐.wav");
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
  expect(uploads).toEqual([true, true]);
});

test("failed BGM link import shows the reason and keeps the original input", async ({ page, request }) => {
  expect((await request.post("/test/bgm-account", { data: {} })).ok()).toBeTruthy();
  const source = "分享文字 https://example.com/video/123";
  const job = { id: "controlled-failure", status: "failed", source_url: source, message: "受控导入失败" };
  await page.route(`**${root}/imports`, async route => {
    if (route.request().method() !== "POST") return route.continue();
    expect(route.request().postDataJSON().source_url).toBe(source);
    await route.fulfill({ status: 202, json: job });
  });
  await page.route(`**${root}/imports/${job.id}`, route => route.fulfill({ status: 200, json: job }));
  await page.goto(`/?creator=${creatorId}`);
  await page.getByRole("button", { name: "BGM", exact: true }).click();
  const input = page.getByRole("textbox", { name: "添加分享链接" });
  await input.fill(source);
  await page.getByRole("button", { name: "添加", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("受控导入失败");
  await expect(input).toHaveValue(source);
});
