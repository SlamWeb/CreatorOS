import { expect, test, type Page } from "@playwright/test";

// Controlled transport tests: no DeepSeek or Codex calls. Real clipboard events,
// file inputs, routing, drafts and rendered messages are exercised in Chromium.
const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aYh8AAAAASUVORK5CYII=", "base64");
type Attachment = { id: string; name: string; mime_type: string; size: number; width: number; height: number; url: string };
type Session = { id: string; creator_id: string | null; scope_kind: string; title: string; version: number; status: string; error: null; entries: unknown[]; updated_at: string; has_older: boolean };
async function controlledChat(page: Page) {
  const sessions = new Map<string, Session>();
  const attachments = new Map<string, Attachment>();
  const imageBytes = new Map<string, Buffer>();
  const posts: { session: string; body: { text: string; attachment_ids: string[] } }[] = [];
  const controls = { failUpload: false, failSend: false, duplicateUpload: false, holdUpload: null as Promise<void> | null, uploads: 0 };
  await page.route("**/api/agent/sessions**", async route => {
    const req = route.request();
    const url = new URL(req.url());
    const sid = url.pathname.split("/")[4];
    if (url.pathname.endsWith("/events")) return route.abort();
    if (url.pathname.endsWith("/attachments") && req.method() === "POST") {
      controls.uploads++;
      const body = req.postDataJSON();
      expect(body.data_base64).not.toContain("data:image");
      if (controls.holdUpload) await controls.holdUpload;
      if (controls.failUpload) return route.fulfill({ status: 503, json: { error: { message: "受控上传中断" } } });
      if (controls.duplicateUpload) return route.fulfill({ status: 201, json: [...attachments.values()].find(image => image.url.includes(`/sessions/${sid}/`)) });
      const id = `image-${attachments.size + 1}`;
      const image = { id, name: body.name, mime_type: "image/png", size: png.length, width: 1, height: 1, url: `/api/agent/sessions/${sid}/attachments/${id}` };
      attachments.set(id, image);
      imageBytes.set(id, Buffer.from(body.data_base64, "base64"));
      return route.fulfill({ status: 201, json: image });
    }
    if (url.pathname.includes("/attachments/")) return route.fulfill({ contentType: "image/png", body: imageBytes.get(url.pathname.split("/").at(-1)!) ?? png });
    if (url.pathname.endsWith("/turns")) {
      const body = req.postDataJSON(); posts.push({ session: sid, body });
      if (controls.failSend) return route.fulfill({ status: 503, json: { error: { message: "受控发送失败" } } });
      const target = sessions.get(sid)!;
      target.version++;
      target.entries.push({ kind: "user", text: body.text, attachments: body.attachment_ids.map((id: string) => attachments.get(id)) });
      return route.fulfill({ json: target });
    }
    if (sid) return route.fulfill({ json: sessions.get(sid) });
    if (req.method() === "POST") {
      const creator = req.postDataJSON().creator_id;
      const id = `image-session-${sessions.size + 1}`;
      const value = { id, creator_id: creator, scope_kind: creator ? "creator" : "overview", title: `图片对话 ${sessions.size + 1}`, version: 1, status: "idle", error: null, entries: [], updated_at: "2026-10-15T08:00:00Z", has_older: false };
      sessions.set(id, value);
      return route.fulfill({ status: 201, json: value });
    }
    return route.fulfill({ json: { items: [...sessions.values()].filter(item => item.creator_id === url.searchParams.get("creator_id")) } });
  });
  return { sessions, attachments, posts, controls };
}
const select = (page: Page, name = "参考.png") => page.getByLabel("选择图片文件").setInputFiles({ name, mimeType: "image/png", buffer: png });

test("real Ctrl+V image paste uploads to a session, sends once and survives refresh", async ({ page, context }, info) => {
  const state = await controlledChat(page);
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto("/agent");
  const input = page.getByRole("textbox", { name: "给 Agent 的消息" });
  await page.evaluate(async () => {
    const canvas = document.createElement("canvas"); canvas.width = 24; canvas.height = 24;
    canvas.getContext("2d")!.fillRect(0, 0, 24, 24);
    const blob = await new Promise<Blob>(resolve => canvas.toBlob(value => resolve(value!), "image/png"));
    await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
  });
  await input.focus(); await page.keyboard.press("Control+v");
  const draft = page.getByLabel("待发送图片");
  await expect(draft.getByRole("img")).toHaveCount(1);
  expect(state.sessions.size).toBe(1);
  await expect(page).toHaveURL(/chat=image-session-1/);
  await expect(page.getByRole("button", { name: "发送 ↑" })).toBeEnabled();
  const stored = await page.evaluate(() => JSON.stringify(sessionStorage));
  expect(stored).not.toContain("base64");
  await page.reload();
  await expect(draft.getByRole("img")).toHaveCount(1);
  await page.screenshot({ path: info.outputPath("images-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: info.outputPath("images-mobile.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("button", { name: "发送 ↑" }).dblclick();
  await expect.poll(() => state.posts.length).toBe(1);
  expect(state.posts[0].body).toMatchObject({ text: "", attachment_ids: ["image-1"] });
  await expect(draft).toHaveCount(0);
  await expect(page.getByLabel("消息图片").getByRole("img")).toHaveCount(1);
  await page.reload();
  await expect(page.getByLabel("消息图片").getByRole("img")).toHaveCount(1);
  await expect(page.getByLabel("消息图片").getByRole("link")).toHaveAttribute("target", "_blank");
  await input.focus();
  await page.evaluate(() => navigator.clipboard.writeText("普通文字粘贴"));
  await page.keyboard.press("Control+v");
  await expect(input).toHaveValue("普通文字粘贴");
  expect(state.controls.uploads).toBe(1);
});

test("upload/send failures retain successful attachments; limits and removal are usable", async ({ page }) => {
  const state = await controlledChat(page);
  await page.goto("/agent");
  await select(page);
  await expect(page.getByLabel("待发送图片").getByRole("img")).toHaveCount(1);
  state.controls.failUpload = true;
  await select(page, "失败.png");
  await expect(page.getByRole("alert")).toContainText("受控上传中断");
  await expect(page.getByLabel("待发送图片").getByRole("img")).toHaveCount(1);
  state.controls.failSend = true;
  await page.getByRole("textbox", { name: "给 Agent 的消息" }).fill("保留这段要求");
  await page.getByRole("button", { name: "发送 ↑" }).click();
  await expect(page.getByRole("alert")).toContainText("受控发送失败");
  await expect(page.getByRole("textbox", { name: "给 Agent 的消息" })).toHaveValue("保留这段要求");
  await expect(page.getByLabel("待发送图片").getByRole("img")).toHaveCount(1);
  await page.getByLabel("选择图片文件").setInputFiles({ name: "过大.png", mimeType: "image/png", buffer: Buffer.alloc(4 * 1024 * 1024 + 1) });
  await expect(page.getByRole("alert")).toContainText("超过 4 MiB");
  await page.getByLabel("选择图片文件").setInputFiles(Array.from({ length: 6 }, (_, n) => ({ name: `${n}.png`, mimeType: "image/png", buffer: png })));
  await expect(page.getByRole("alert")).toContainText("最多 6 张");
  expect(state.controls.uploads).toBe(2);
  await page.getByRole("button", { name: "移除图片 参考.png" }).click();
  await expect(page.getByLabel("待发送图片")).toHaveCount(0);
});

test("an upload cannot send mid-flight or leak to another history session", async ({ page }) => {
  const state = await controlledChat(page);
  await page.goto("/agent");
  let release!: () => void;
  state.controls.holdUpload = new Promise<void>(resolve => { release = resolve; });
  await select(page);
  await expect(page.getByRole("status")).toContainText("正在上传图片");
  await expect(page.getByRole("button", { name: "发送 ↑" })).toBeDisabled();
  await page.locator(".agent-new").click();
  release();
  await expect.poll(() => state.attachments.size).toBe(1);
  await expect(page).not.toHaveURL(/chat=/);
  await expect(page.getByLabel("待发送图片")).toHaveCount(0);
  await page.getByRole("button", { name: /图片对话 1/ }).click();
  await expect(page.getByLabel("待发送图片").getByRole("img")).toHaveCount(1);
  expect(state.posts).toHaveLength(0);
});

test("Trace renders current-session image references without loading foreign images", async ({ page }) => {
  const state = await controlledChat(page);
  await page.goto("/agent"); await select(page);
  await expect(page.getByLabel("待发送图片").getByRole("img")).toHaveCount(1);
  const image = state.attachments.get("image-1")!;
  const session = state.sessions.get("image-session-1")!;
  session.entries.push({ kind: "assistant", text: "已检查图片。", complete: true, turn_id: "image-turn", model_request_id: "image-request" });
  await page.route("**/turn-trace/**", route => route.fulfill({ json: route.request().url().includes("/requests/") ? {
    schema_version: 1, turn_id: "image-turn", request_id: "image-request", context: {
      messages: [{ role: "user", content: [
        { type: "text", text: "这是实际请求中的参考图" }, { type: "image_ref", attachment: image },
        { type: "image_ref", attachment: { ...image, url: "/api/agent/sessions/other/attachments/image-1" } },
        { type: "image_ref", attachment: { ...image, url: "https://evil.invalid/image.png" } },
      ] }], tools: [], max_output_tokens: 1000,
    }, response: null, tool_results: [], redacted: false,
  } : { turn_id: "image-turn", status: "complete", available: true, requests: [{ request_id: "image-request", request_kind: "main", event: "finished", status: "complete", snapshot_available: true }] } }));
  let foreignLoads = 0;
  page.on("request", req => { if (req.url().includes("evil.invalid") || req.url().includes("/sessions/other/attachments/")) foreignLoads++; });
  await page.reload(); await page.getByRole("button", { name: "查看回复 Trace" }).click();
  const trace = page.getByRole("dialog", { name: "回复 Trace" });
  await expect(trace.getByRole("img")).toHaveCount(1);
  await expect(trace.getByText("这是实际请求中的参考图", { exact: false })).toBeVisible();
  await trace.getByRole("button", { name: "原始消息" }).click();
  await expect(trace.locator("code").filter({ hasText: '"role": "user"' })).toContainText("image_ref");
  expect(foreignLoads).toBe(0);
});

test("account drawer keeps image drafts with their own session across account switches", async ({ page, request }) => {
  const state = await controlledChat(page);
  const a = await (await request.post("/api/creators", { data: { display_name: "图片隔离甲" } })).json();
  const b = await (await request.post("/api/creators", { data: { display_name: "图片隔离乙" } })).json();
  await page.goto(`/?creator=${a.id}`);
  await page.getByRole("button", { name: "打开账号对话" }).click();
  await select(page, "甲.png");
  await expect(page.getByLabel("待发送图片").getByRole("img", { name: "甲.png" })).toBeVisible();
  await page.getByRole("button", { name: `查看账号 ${b.display_name}` }).click();
  await expect(page.getByLabel("待发送图片")).toHaveCount(0);
  await select(page, "乙.png");
  await expect(page.getByLabel("待发送图片").getByRole("img", { name: "乙.png" })).toBeVisible();
  await page.getByRole("button", { name: `查看账号 ${a.display_name}` }).click();
  await expect(page.getByLabel("待发送图片").getByRole("img", { name: "甲.png" })).toBeVisible();
  await expect(page.getByRole("img", { name: "乙.png" })).toHaveCount(0);
  await page.reload();
  await page.getByRole("button", { name: "打开账号对话" }).click();
  await expect(page.getByLabel("待发送图片").getByRole("img", { name: "甲.png" })).toBeVisible();
  expect([...state.sessions.values()].map(value => value.creator_id)).toEqual([a.id, b.id]);
  expect(state.posts).toHaveLength(0);
});

test("the same stored image pasted again stays a single attachment", async ({ page }) => {
  const state = await controlledChat(page);
  await page.goto("/agent"); await select(page);
  await expect(page.getByLabel("待发送图片").getByRole("img")).toHaveCount(1);
  state.controls.duplicateUpload = true;
  await select(page);
  await expect.poll(() => state.controls.uploads).toBe(2);
  await expect(page.getByRole("button", { name: "发送 ↑" })).toBeEnabled();
  await expect(page.getByLabel("待发送图片").getByRole("img")).toHaveCount(1);
  await page.getByRole("button", { name: "发送 ↑" }).click();
  await expect.poll(() => state.posts.length).toBe(1);
  expect(state.posts[0].body.attachment_ids).toEqual(["image-1"]);
});
