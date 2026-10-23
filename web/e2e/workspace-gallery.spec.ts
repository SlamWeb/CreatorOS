import { expect, test, type APIRequestContext } from "@playwright/test";

async function fixture(request: APIRequestContext) {
  const create = async (url: string, data: object) => {
    const response = await request.post(url, { data });
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  };
  const suffix = crypto.randomUUID().slice(0, 6);
  const account = await create("/api/creators", { display_name: `内容流验收甲 ${suffix}` });
  const other = await create("/api/creators", { display_name: `内容流验收乙 ${suffix}` });
  const series = await create(`/api/creators/${account.id}/series`, { name: "有图与草稿" });
  const second = await create(`/api/creators/${account.id}/series`, { name: "同账号第二栏目" });
  const external = await create(`/api/creators/${other.id}/series`, { name: "另一个账号的栏目" });
  const topics = await create(`/api/series/${series.id}/queue`, { request_id: crypto.randomUUID(), topics: [
    { title: "已经完成的真实文件卡片", brief: "这张卡片有三个经过接口校验的测试图片。" },
    { title: "还没有生产的文字草稿", brief: "不应该显示空白图片框，也不能自动创建 Run。" },
  ] });
  await create(`/api/series/${second.id}/queue`, { request_id: crypto.randomUUID(), topics: [{ title: "同账号另一个选题" }] });
  await create(`/api/series/${external.id}/queue`, { request_id: crypto.randomUUID(), topics: [{ title: "乙账号私有选题" }] });
  const run = await create("/api/runs", { topic_id: topics.topic_ids[0] });
  // This test executable uses E2EProducer; it cannot call Codex or publish.
  await create(`/api/runs/${run.id}/execute`, { expected_version: run.version });
  await expect.poll(async () => (await request.get(`/api/runs/${run.id}`).then(r => r.json())).status).toBe("awaiting_approval");
  return { account, other, series, second, run, topicIds: topics.topic_ids as string[] };
}

test("account gallery uses validated covers; detail return preserves filter and focus without writes", async ({ page, request }, info) => {
  const data = await fixture(request);
  const writes: string[] = [];
  const reads = { run: 0, research: 0, overview: 0, library: 0 };
  page.on("request", event => {
    const url = new URL(event.url());
    if (event.method() !== "GET") writes.push(url.pathname);
    if (url.pathname === `/api/runs/${data.run.id}`) reads.run++;
    if (url.pathname.endsWith("/topic-research")) reads.research++;
    if (url.pathname === "/api/overview") reads.overview++;
    if (url.pathname.endsWith("/topic-library")) reads.library++;
  });
  await page.goto(`/?creator=${data.account.id}&topics=queued`);
  await expect(page.locator(".content-card")).toHaveCount(3);
  await expect(page.locator(".workspace-main").getByText("乙账号私有选题")).toHaveCount(0);
  const produced = page.getByTestId(`library-${data.topicIds[0]}`);
  const cover = produced.getByRole("img");
  await expect(cover).toBeVisible();
  await expect.poll(() => cover.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBe(720);
  await expect(produced).toHaveCSS("background-color", "rgb(255, 255, 255)");
  const draft = page.getByTestId(`library-${data.topicIds[1]}`);
  await expect(draft.getByRole("img")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /上移|下移/ })).toHaveCount(0);
  expect(reads.run).toBe(0); // Covers are a bulk projection, not N RunDetail calls.
  expect(reads.research).toBe(0);
  expect(reads.overview).toBe(0);
  const baselineLibraryReads = reads.library;
  await page.waitForTimeout(11_000);
  expect(reads.library).toBe(baselineLibraryReads);
  expect(writes).toEqual([]);
  await page.screenshot({ path: info.outputPath("workspace-gallery-desktop.png"), fullPage: true });

  await produced.getByRole("link", { name: "查看内容 已经完成的真实文件卡片" }).click();
  await expect(page.getByRole("heading", { name: "已经完成的真实文件卡片" })).toBeVisible();
  await page.getByRole("button", { name: "查看第 2 张", exact: true }).click();
  await expect(page.locator(".carousel-nav")).toContainText("2 / 3");
  await page.getByRole("button", { name: "技术记录", exact: true }).click();
  await expect(page.getByText("文件检查通过", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "返回栏目", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`creator=${data.account.id}&topics=queued`));
  await expect(page.locator(`#topic-card-${data.topicIds[0]}`)).toBeFocused();
  await page.reload();
  await expect(page.getByRole("group", { name: "生产进度" })).toHaveCount(0);
  await produced.locator("summary").click();
  await produced.getByRole("button", {name:"详情与编辑"}).click();
  await page.getByRole("link", {name:"查看内容与生产记录"}).click();
  await page.getByRole("link", {name:"返回栏目", exact:true}).click();
  await expect(page).toHaveURL(new RegExp(`creator=${data.account.id}&topics=queued$`));
  await expect(page.getByRole("dialog", {name:"选题详情"})).toHaveCount(0);
  await expect(page.locator(`#topic-card-${data.topicIds[0]}`)).toBeFocused();
  expect(writes).toEqual([]);

  await page.locator(".rail-series").filter({ hasText: "有图与草稿" }).click();
  await expect(page.locator(".content-card")).toHaveCount(2);
  await expect(page.getByRole("heading", { name: "同账号另一个选题" })).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("button", { name: /账号与栏目 ·/ })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("workspace-gallery-mobile.png"), fullPage: true });
  await page.setViewportSize({ width: 320, height: 700 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("unproduced detail edits the real topic, discussion only prefills, unreadable cover is explicit", async ({ page, request }) => {
  const data = await fixture(request);
  const writes: string[] = [];
  page.on("request", event => { if (event.method() !== "GET") writes.push(new URL(event.url()).pathname); });
  await page.route("**/cards/1?**", route => route.fulfill({ status: 404, body: "fixture: missing image" }));
  await page.goto(`/?creator=${data.account.id}&series=${data.series.id}&topics=all`);
  await expect(page.getByText("封面暂不可读取")).toBeVisible();
  await page.getByRole("button", { name: "查看选题 还没有生产的文字草稿" }).click();
  const dialog = page.getByRole("dialog", { name: "选题详情" });
  await expect(dialog).toBeVisible();
  expect((await request.get(`/api/series/${data.series.id}/topics`).then(r => r.json())).items[1].existing_run_id).toBeNull();
  await dialog.getByRole("button", { name: "编辑", exact: true }).click();
  await dialog.getByLabel("标题", { exact: true }).fill("已通过详情修改的草稿");
  await dialog.getByLabel("切入点", { exact: true }).fill("保留真实来源，不启动生产。");
  await dialog.getByRole("button", { name: "保存", exact: true }).click();
  await expect(dialog.getByRole("heading", { name: "已通过详情修改的草稿" })).toBeVisible();
  await dialog.getByRole("button", { name: "与 Agent 讨论" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByLabel("给 Agent 的消息")).toHaveValue("我们讨论一下选题「已通过详情修改的草稿」。");
  expect(writes).toEqual([`/api/topics/${data.topicIds[1]}`]);
  await page.keyboard.press("Escape");
  await expect(page.locator(".account-chat-panel")).toBeHidden();
  await expect(page.getByRole("button", { name: "打开账号对话" })).toBeFocused();
  await page.getByRole("button", { name: "打开账号对话" }).click();
  await expect(page.getByLabel("给 Agent 的消息")).toHaveValue(/已通过详情修改的草稿/);
});

test("explicit assignment follows the confirmed owner and does not reuse another account chat", async ({ page, request }) => {
  const data = await fixture(request);
  await page.goto(`/?creator=${data.account.id}&series=${data.second.id}`);
  await page.getByRole("button", { name: "栏目设置", exact: true }).click();
  await page.getByRole("combobox", { name: "归属账号" }).selectOption(data.other.id);
  await expect.poll(() => new URL(page.url()).searchParams.get("creator")).toBe(data.other.id);
  await expect(page.getByRole("heading", { name: "同账号第二栏目", level: 1 })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  const stored = await request.get(`/api/series/${data.second.id}`).then(r => r.json());
  expect(stored.creator_id).toBe(data.other.id);
  await page.getByRole("button", { name: "打开账号对话" }).click();
  await expect(page.locator(".account-chat-panel").getByRole("heading", { name: data.other.display_name, exact: true })).toBeVisible();
  await page.getByRole("button", { name: "关闭账号对话" }).click();
  await page.goto(`/?creator=${data.account.id}&series=${data.second.id}`);
  await expect(page.getByRole("alert")).toContainText("不属于当前账号");
  await expect(page.getByRole("heading", { name: "同账号另一个选题" })).toHaveCount(0);
});

// Controlled chat transport: these checks never send a turn to a model.
test("topic discussion freezes the exact target through refresh and unknown recovery, then clears it", async ({ page, request }) => {
  const data = await fixture(request);
  const writes: string[] = [];
  const submitted: Record<string, unknown>[] = [];
  let chat: Record<string, unknown> | undefined;
  page.on("request", event => { if (event.method() !== "GET") writes.push(new URL(event.url()).pathname); });
  await page.route("**/api/agent/sessions/*", route => {
    const identity = new URL(route.request().url()).pathname.split("/").at(-1);
    return chat?.id === identity ? route.fulfill({ json: chat }) : route.continue();
  });
  await page.route("**/api/agent/sessions/*/turns", async route => {
    const payload = route.request().postDataJSON();
    submitted.push(payload);
    const url = new URL(route.request().url()).pathname.replace(/\/turns$/, "");
    chat ??= await request.get(url).then(response => response.json());
    if (submitted.length === 1) { await route.abort("failed"); return; }
    chat = { ...chat, version: submitted.length * 2, status: "idle", entries: [
      { kind: "user", text: payload.text, turn_id: payload.request_id },
    ] };
    await route.fulfill({ json: chat });
  });
  await page.goto(`/?creator=${data.account.id}&series=${data.series.id}&topics=all`);
  const card = page.getByTestId(`library-${data.topicIds[0]}`);
  await card.locator("summary").click();
  await card.getByRole("button", { name: "与 Agent 讨论" }).click();
  const panel = page.locator(".account-chat-panel");
  await expect(panel.getByRole("group", { name: "本次选题" })).toContainText("已经完成的真实文件卡片");
  await expect(panel.getByRole("textbox", { name: "给 Agent 的消息" })).toHaveValue("我们讨论一下选题「已经完成的真实文件卡片」。");
  expect(writes).toEqual([]); // Opening the discussion neither creates a session nor submits a turn.
  await page.reload();
  await expect(panel.getByRole("group", { name: "本次选题" })).toContainText("已经完成的真实文件卡片");
  expect(writes).toEqual([]);
  await panel.getByRole("button", { name: "发送 ↑" }).click();
  await expect(panel.getByRole("button", { name: "核对并恢复上次提交" })).toBeVisible();
  const target = { series_id: data.series.id, topic_id: data.topicIds[0], run_id: data.run.id };
  expect(submitted[0].context_target).toEqual(target);
  await page.reload();
  await expect(panel.getByRole("button", { name: "移除选题焦点" })).toBeDisabled();
  expect(submitted).toHaveLength(1);
  await panel.getByRole("button", { name: "核对并恢复上次提交" }).click();
  await expect(panel.getByRole("button", { name: "核对并恢复上次提交" })).toHaveCount(0);
  expect(submitted).toHaveLength(2);
  expect(submitted[1]).toEqual(submitted[0]);
  await expect(panel.getByRole("group", { name: "本次选题" })).toHaveCount(0);
  await panel.getByRole("textbox", { name: "给 Agent 的消息" }).fill("接下来只讨论栏目安排");
  await panel.getByRole("button", { name: "发送 ↑" }).click();
  await expect.poll(() => submitted.length).toBe(3);
  expect(submitted[2]).not.toHaveProperty("context_target");
});

test("topic focus stays with its account and rejected draft, and can be removed or reset", async ({ page, request }, info) => {
  const data = await fixture(request);
  const writes: string[] = [];
  page.on("request", event => { if (event.method() !== "GET") writes.push(new URL(event.url()).pathname); });
  await page.route("**/api/agent/sessions/*/turns", route => route.abort("blockedbyclient"));
  await page.goto(`/?creator=${data.account.id}&series=${data.series.id}&topics=all`);
  const produced = page.getByTestId(`library-${data.topicIds[0]}`);
  await produced.locator("summary").click();
  await produced.getByRole("button", { name: "与 Agent 讨论" }).click();
  const panel = page.locator(".account-chat-panel");
  const focus = panel.getByRole("group", { name: "本次选题" });
  await expect(focus).toContainText("已经完成的真实文件卡片");
  await panel.getByRole("button", { name: "关闭账号对话" }).click();
  const draft = page.getByTestId(`library-${data.topicIds[1]}`);
  await draft.locator("summary").click();
  page.once("dialog", dialog => dialog.dismiss());
  await draft.getByRole("button", { name: "与 Agent 讨论" }).click();
  await expect(focus).toContainText("已经完成的真实文件卡片");
  await expect(panel.getByRole("textbox", { name: "给 Agent 的消息" })).toHaveValue("我们讨论一下选题「已经完成的真实文件卡片」。");
  await page.getByRole("button", { name: `查看账号 ${data.other.display_name}` }).click();
  await expect(panel.getByRole("heading", { name: data.other.display_name, exact: true })).toBeVisible();
  await expect(focus).toHaveCount(0);
  await expect(panel.getByRole("textbox", { name: "给 Agent 的消息" })).toHaveValue("");
  await page.getByRole("button", { name: `查看账号 ${data.account.display_name}` }).click();
  await expect(focus).toContainText("已经完成的真实文件卡片");
  await page.screenshot({ path: info.outputPath("topic-focus-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(panel.getByRole("button", { name: "移除选题焦点" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("topic-focus-mobile.png"), fullPage: true });
  await panel.getByRole("button", { name: "移除选题焦点" }).click();
  await expect(focus).toHaveCount(0);
  await expect(panel.getByRole("textbox", { name: "给 Agent 的消息" })).toHaveValue(/已经完成/);
  await page.reload();
  await expect(focus).toHaveCount(0);
  await page.setViewportSize({ width: 1440, height: 900 });
  await panel.getByRole("button", { name: "关闭账号对话" }).click();
  await produced.locator("summary").click();
  await produced.getByRole("button", { name: "与 Agent 讨论" }).click();
  await expect(focus).toBeVisible();
  await panel.getByRole("button", { name: "新对话", exact: true }).click();
  await expect(focus).toHaveCount(0);
  expect(writes).toEqual([]);
});

test("a pending suggestion discusses its title without claiming a formal topic identity", async ({ page, request }) => {
  const data = await fixture(request);
  const title = "尚未入队的柯基候选";
  const writes: string[] = [];
  let payload: Record<string, unknown> | undefined;
  page.on("request", event => { if (event.method() !== "GET") writes.push(new URL(event.url()).pathname); });
  await page.route(`**/api/series/${data.series.id}/topic-library**`, route => route.fulfill({ json: {
    items: [{ id: "candidate-preview-only", title, selection_state: "pending", batch_id: "batch-a", candidate_id: "candidate-a",
      angle: "待用户选择", rationale: "受控展示候选", stale: false, sources: [], available_actions: ["prepare_topic_selection"] }],
    page: { offset: 0, limit: 20, total: 1, has_more: false, next_offset: null },
  } }));
  await page.route("**/api/agent/sessions/*/turns", route => {
    payload = route.request().postDataJSON();
    return route.fulfill({ status: 503, json: { error: { message: "受控传输：不调用模型" } } });
  });
  await page.goto(`/?creator=${data.account.id}&series=${data.series.id}`);
  const card = page.getByTestId("library-candidate-preview-only");
  await card.locator("summary").click();
  await card.getByRole("button", { name: "与 Agent 讨论" }).click();
  const panel = page.locator(".account-chat-panel");
  await expect(panel.getByRole("textbox", { name: "给 Agent 的消息" })).toHaveValue(`我们讨论一下选题「${title}」。`);
  await expect(panel.getByRole("group", { name: "本次选题" })).toHaveCount(0);
  expect(writes).toEqual([]);
  await panel.getByRole("button", { name: "发送 ↑" }).click();
  await expect.poll(() => payload).toBeTruthy();
  expect(payload).not.toHaveProperty("context_target");
  expect(writes).toHaveLength(2); // One explicitly requested session creation and one intercepted turn.
});
