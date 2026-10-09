import { expect, test, type Page } from "@playwright/test";

type Node = { id: string; label: string; kind: string; has_children: boolean; status?: string };
const node = (id: string, label: string, kind: string, has_children = false): Node => ({ id, label, kind, has_children });
const account = node("account-a", "受控账号 · 后端笔记", "creator", true);
const session = node("session-a", "解释消息队列", "session", true);
const turn = node("user-a", "查看调研并解释重试", "user_turn", true);
const requestNode = node("request-a", "模型请求 1", "request", true);
const tool = node("tool-a", "start_research · 调研工具", "tool_call");
const research = node("research-a", "消息队列选题调研", "research", true);
const codexTurn = node("codex-a", "Codex 执行回合 1", "codex_turn", true);
const publicItem = node("item-a", "搜索结果已整理", "codex_item");
const longText = "完整公开输入。".repeat(180) + "末尾验收标记。";
const byId = Object.fromEntries([account, session, turn, requestNode, tool, research, codexTurn, publicItem].map(item => [item.id, item]));
const tree: Record<string, Node[]> = {
  root: [account], "account-a": [session, research], "session-a": [turn], "user-a": [requestNode],
  "request-a": [tool], "research-a": [codexTurn], "codex-a": [publicItem],
};
const paths: Record<string, Node[]> = {
  "account-a": [], "session-a": [account], "user-a": [account, session],
  "request-a": [account, session, turn], "tool-a": [account, session, turn, requestNode],
  "research-a": [account], "codex-a": [account, research], "item-a": [account, research, codexTurn],
};

function detail(id: string) {
  const item = byId[id] ?? node(id, id, "record");
  return {
    ...item, status: "completed", active: false, breadcrumbs: paths[id] ?? [], warnings: id === "item-a" ? ["历史记录仅保留公开摘要，完整 SDK 事件缺失。"] : [],
    sections: [{ title: "完整输入", content: { messages: [{ role: "user", content: longText }] } }, { title: "返回数据", content: { html: "<img src='https://never-load.invalid/image.png' onerror='window.observationInjected=1'>", result: "![远图](https://never-load.invalid/other.png)", value: 7 } }],
    timeline: [{ id: `${id}-start`, label: "开始读取已保存输入", kind: "message", at: "2026-10-08T01:00:00Z", content: "这条记录来自受控浏览器夹具。" },
      { id: `${id}-reply`, label: id === "item-a" ? "公开回复步骤" : "公开结果", kind: "message", at: "2026-10-08T01:00:01Z", content: id === "item-a" ? "候选已经整理完成。" : longText },
      { id: `${id}-snapshot`, label: "实际输入快照", kind: "input", at: "2026-10-08T01:00:02Z", content: { text: "对象中的公开文字可直接阅读。", messages: [{ role: "user", content: longText }] } }],
    links: id === "tool-a" ? [{ label: "查看关联调研", node_id: research.id }] : id === "research-a" ? [{ label: "查看 Codex 执行回合", node_id: codexTurn.id }] : id === "codex-a" ? [{ label: "查看公开步骤", node_id: publicItem.id }] : id === "request-a" ? [{ label: "查看工具调用", node_id: tool.id }] : [],
  };
}

async function controlledTree(page: Page) {
  await page.route("**/api/observation/tree?*", route => {
    const url = new URL(route.request().url());
    const parent = url.searchParams.get("parent") ?? "root";
    const offset = Number(url.searchParams.get("offset") ?? 0);
    if (parent === "root" && offset === 100) return route.fulfill({ json: { items: [node("page-two", "分页后的历史账号", "creator")], next_offset: null, has_more: false } });
    return route.fulfill({ json: { items: tree[parent] ?? [], next_offset: parent === "root" ? 100 : null, has_more: parent === "root" } });
  });
}

test("real isolated API: account, series and empty session are observable without model requests", async ({ page, request }, info) => {
  const created = await request.post("/api/creators", { data: { display_name: "Observation 真实隔离账号" } });
  expect(created.status()).toBe(201);
  const creator = await created.json() as { id: string; display_name: string };
  const seriesResponse = await request.post(`/api/creators/${creator.id}/series`, { data: { name: "Observation 真实隔离栏目" } });
  expect(seriesResponse.status()).toBe(201);
  const sessionResponse = await request.post("/api/agent/sessions", { data: { creator_id: creator.id } });
  expect(sessionResponse.status()).toBe(201);
  const savedSession = await sessionResponse.json() as { id: string; title: string };
  const writes: string[] = [];
  page.on("request", event => { if (new URL(event.url()).pathname.startsWith("/api/") && event.method() !== "GET") writes.push(event.url()); });
  await page.goto("/observation");
  await expect(page.getByRole("navigation", { name: "主导航" }).getByRole("link", { name: "Observation", exact: true })).toHaveClass(/active/);
  await page.getByRole("button", { name: `展开${creator.display_name}`, exact: true }).click();
  const nav = page.getByRole("complementary", { name: "记录导航" });
  await nav.getByRole("button", { name: "Observation 真实隔离栏目 栏目", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Observation 真实隔离栏目", exact: true })).toBeVisible();
  const selectedSeries = new URL(page.url()).searchParams.get("node");
  expect(selectedSeries).toBeTruthy();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Observation 真实隔离栏目", exact: true })).toBeVisible();
  expect(new URL(page.url()).searchParams.get("node")).toBe(selectedSeries);
  await nav.getByRole("button", { name: "展开账号 Agent 会话", exact: true }).click();
  await nav.locator(".observation-node-select").filter({ hasText: savedSession.title }).click();
  await expect(page.getByRole("heading", { name: savedSession.title, exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "观察详情" })).toContainText("暂无");
  await page.getByRole("button", { name: "刷新记录", exact: true }).click();
  await expect(page.getByRole("region", { name: "观察详情" })).not.toHaveAttribute("aria-busy", "true");
  expect(writes).toEqual([]);
  await page.screenshot({ path: info.outputPath("observation-real-api-desktop.png"), fullPage: true });
});

test("controlled timeline: deep links, pagination, full input, linked worker steps and mobile keyboard", async ({ page }, info) => {
  const writes: string[] = [];
  const remoteImages: string[] = [];
  page.on("request", event => {
    if (new URL(event.url()).pathname.startsWith("/api/") && event.method() !== "GET") writes.push(event.url());
    if (event.url().includes("never-load.invalid")) remoteImages.push(event.url());
  });
  await controlledTree(page);
  await page.route("**/api/observation/detail?*", route => route.fulfill({ json: detail(new URL(route.request().url()).searchParams.get("node_id")!) }));
  await page.goto("/observation?node=request-a");
  const panel = page.getByRole("region", { name: "观察详情" });
  const nav = page.getByRole("complementary", { name: "记录导航" });
  await expect(panel.getByRole("heading", { name: requestNode.label, exact: true })).toBeVisible();
  await expect(nav.getByRole("button", { name: "模型请求 1 模型请求", exact: true })).toHaveAttribute("aria-current", "true");
  await expect(panel.locator(".observation-record").first().getByRole("button", { name: "完整输入", exact: true })).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator(".observation-page summary, .observation-node-toggle .lucide-chevron-right, .observation-node-toggle .lucide-chevron-down")).toHaveCount(0);
  const eventPayload = panel.locator(".observation-event-payload");
  await expect(eventPayload.getByRole("button", { name: "查看输入 / 输出", exact: true })).toHaveAttribute("aria-expanded", "false");
  await expect(panel.getByText("对象中的公开文字可直接阅读。", { exact: true }).first()).toBeVisible();
  await panel.getByRole("button", { name: /展开全文/ }).click();
  await expect(panel.locator(".observation-timeline").getByText(longText, { exact: true })).toBeVisible();
  await eventPayload.getByRole("button", { name: "查看输入 / 输出", exact: true }).click();
  await eventPayload.getByRole("button", { name: /展开全文/ }).click();
  await expect(eventPayload.getByText(longText, { exact: true })).toBeVisible();
  await eventPayload.getByRole("button", { name: "查看输入 / 输出", exact: true }).click();
  await panel.getByRole("button", { name: "完整输入", exact: true }).click();
  await panel.locator('.observation-record[data-open="true"]').getByRole("button", { name: /展开全文/ }).click();
  await expect(panel.locator('.observation-record[data-open="true"]').getByText(longText, { exact: true })).toBeVisible();
  await panel.getByRole("button", { name: "返回数据", exact: true }).click();
  await expect(panel.locator("img, iframe, script")).toHaveCount(0);
  expect(remoteImages).toEqual([]);
  await nav.getByRole("button", { name: "加载更多记录", exact: true }).click();
  await expect(nav.getByRole("button", { name: "分页后的历史账号 账号", exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath("observation-controlled-desktop.png"), fullPage: true });

  await panel.getByRole("button", { name: "查看工具调用", exact: true }).click();
  await panel.getByRole("button", { name: "查看关联调研", exact: true }).click();
  await expect(panel.getByRole("heading", { name: research.label, exact: true })).toBeVisible();
  await panel.getByRole("button", { name: "查看 Codex 执行回合", exact: true }).click();
  await panel.getByRole("button", { name: "查看公开步骤", exact: true }).click();
  await expect(panel.getByText("历史记录仅保留公开摘要，完整 SDK 事件缺失。", { exact: true })).toBeVisible();
  await expect(panel.getByText("候选已经整理完成。", { exact: true })).toBeVisible();
  await page.goBack();
  await expect(panel.getByRole("heading", { name: codexTurn.label, exact: true })).toBeVisible();
  await page.reload();
  await expect(panel.getByRole("heading", { name: codexTurn.label, exact: true })).toBeVisible();
  const accountButton = nav.getByRole("button", { name: `${account.label} 账号`, exact: true });
  await accountButton.focus();
  await page.keyboard.press("ArrowDown");
  await expect(nav.getByRole("button", { name: `${session.label} Agent 对话`, exact: true })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(panel.getByRole("heading", { name: session.label, exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await nav.getByRole("button", { name: "收起目录", exact: true }).click();
  await expect(nav.locator(".observation-tree")).toBeHidden();
  await nav.getByRole("button", { name: "展开目录", exact: true }).click();
  await expect(nav.locator(".observation-tree")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("observation-controlled-mobile.png"), fullPage: true });
  await page.goto("/observation?node=page-two");
  await expect(nav.getByRole("button", { name: "分页后的历史账号 账号", exact: true })).toHaveAttribute("aria-current", "true");
  expect(writes).toEqual([]);
});

test("controlled failures: retry, missing record, empty tree and late response isolation", async ({ page }) => {
  await controlledTree(page);
  let attempts = 0;
  let releaseLate: (() => void) | undefined;
  const held = new Promise<void>(resolve => { releaseLate = resolve; });
  await page.route("**/api/observation/detail?*", async route => {
    const id = new URL(route.request().url()).searchParams.get("node_id")!;
    if (id === "request-a" && attempts++ === 0) return route.fulfill({ status: 503, json: { error: { message: "受控快照读取失败" } } });
    if (id === "session-a") { await held; return route.fulfill({ json: detail(id) }).catch(() => {}); }
    if (id === "page-two") return route.fulfill({ status: 404, json: { error: { message: "missing" } } });
    return route.fulfill({ json: detail(id) });
  });
  await page.goto("/observation?node=request-a");
  await expect(page.getByRole("alert")).toContainText("受控快照读取失败");
  await page.getByRole("button", { name: "重新读取", exact: true }).click();
  await expect(page.getByRole("heading", { name: requestNode.label, exact: true })).toBeVisible();
  const nav = page.getByRole("complementary", { name: "记录导航" });
  await nav.getByRole("button", { name: `${session.label} Agent 对话`, exact: true }).click();
  await expect(page).toHaveURL(/node=session-a/);
  await nav.getByRole("button", { name: `${research.label} 调研`, exact: true }).click();
  await expect(page.getByRole("heading", { name: research.label, exact: true })).toBeVisible();
  releaseLate!();
  await expect(page.getByRole("region", { name: "观察详情" }).getByRole("heading", { name: session.label, exact: true })).toHaveCount(0);
  await nav.getByRole("button", { name: "加载更多记录", exact: true }).click();
  await nav.getByRole("button", { name: "分页后的历史账号 账号", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("这条记录不存在或已不可用");
  await page.route("**/api/observation/tree?*", route => route.fulfill({ json: { items: [], next_offset: null, has_more: false } }));
  await page.goto("/observation");
  await expect(page.getByText("暂无可观察记录。创建账号或产生任务后会出现在这里。", { exact: true })).toBeVisible();
});

test("controlled live record: updates retain scroll; hidden and terminal records stop polling", async ({ page }) => {
  await controlledTree(page);
  let count = 0;
  let finished = false;
  await page.route("**/api/observation/detail?*", route => {
    count++;
    const value = detail("request-a");
    value.active = !finished;
    value.status = finished ? "completed" : "running";
    value.timeline = Array.from({ length: 15 + count }, (_, index) => ({ id: `event-${index}`, label: `公开事件 ${index + 1}`, kind: "message", at: "2026-10-08T01:00:00Z", content: "可见公开内容。".repeat(10) }));
    return route.fulfill({ json: value });
  });
  await page.goto("/observation?node=request-a");
  await expect(page.getByText("进行中 · 自动更新", { exact: true })).toBeVisible();
  await page.clock.install();
  await page.evaluate(() => window.scrollTo(0, 500));
  const scrollBefore = await page.evaluate(() => window.scrollY);
  const before = count;
  await page.clock.fastForward(4_100);
  await expect.poll(() => count).toBeGreaterThan(before);
  expect(await page.evaluate(() => window.scrollY)).toBe(scrollBefore);
  await page.getByRole("button", { name: "回到最新", exact: true }).click();
  await expect(page.locator(".observation-timeline > li").last()).toBeInViewport();
  const followingCount = count;
  await page.clock.fastForward(4_100);
  await expect.poll(() => count).toBeGreaterThan(followingCount);
  await expect(page.locator(".observation-timeline > li").last()).toBeInViewport();
  await page.evaluate(() => { Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" }); document.dispatchEvent(new Event("visibilitychange")); });
  await expect(page.getByText("页面已隐藏 · 暂停更新", { exact: true })).toBeVisible();
  const hiddenCount = count;
  await page.clock.fastForward(12_000);
  expect(count).toBe(hiddenCount);
  finished = true;
  await page.evaluate(() => { Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" }); document.dispatchEvent(new Event("visibilitychange")); });
  await page.getByRole("button", { name: "刷新记录", exact: true }).click();
  await expect(page.getByText("已保存记录", { exact: true })).toBeVisible();
  const terminalCount = count;
  await page.clock.fastForward(12_000);
  expect(count).toBe(terminalCount);
  await page.getByRole("button", { name: "回到最新", exact: true }).click();
  await page.clock.fastForward(1_000);
  await expect(page.locator(".observation-timeline > li").last()).toBeInViewport();
});
