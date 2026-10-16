import { expect, test, type APIRequestContext } from "@playwright/test";
import type { PublicationRequest } from "../src/api/publication";

async function post(request: APIRequestContext, path: string, data: object) {
  const response = await request.post(path, { data });
  expect(response.ok(), await response.text()).toBe(true);
  return response.json();
}

test("publication preview requires approval and explicit frozen confirmation; uncertain submit is never resent", async ({ page, request }, info) => {
  // Real isolated Studio HTTP/SQLite + deterministic test Producer. Only publication
  // responses are intercepted: this test must never contact or publish to Xiaohongshu.
  const account = await post(request, "/api/creators", { display_name: "发布隔离验收" });
  const series = await post(request, `/api/creators/${account.id}/series`, { name: "英语词汇" });
  const queue = await post(request, `/api/series/${series.id}/queue`, { request_id: crypto.randomUUID(), topics: [{ title: "同是害怕，这4个英语词怎么选？" }] });
  const run = await post(request, "/api/runs", { topic_id: queue.topic_ids[0] });
  await post(request, `/api/runs/${run.id}/execute`, { expected_version: run.version });
  await expect.poll(async () => (await request.get(`/api/runs/${run.id}`).then(r => r.json())).status).toBe("awaiting_approval");
  const stored = await request.get(`/api/runs/${run.id}`).then(r => r.json());
  let saved: PublicationRequest | null = null;
  let sendCount = 0;
  let confirmCount = 0;
  let brokenImage = true;
  let confirmationMode: "reject" | "draft-network-error" | "unknown" = "reject";
  let ready = false;
  await page.route(`**/api/runs/${run.id}/publication-request`, async route => {
    if (route.request().method() === "POST") {
      const payload = route.request().postDataJSON();
      expect(Object.keys(payload).sort()).toEqual(["body", "tags", "title", "visibility"]);
      saved = { id: "test-publication", run_id: run.id, revision_id: stored.revisions[0].id, status: "draft", digest: crypto.randomUUID(),
        account: { id: "isolated-xhs-user", name: "测试账号（不会发布）" }, payload: { ...payload, account_id: "server-only-account", image_count: 3, image_hashes: ["server-only-hash"], images: stored.revisions[0].cards.map((card: { order: number; url: string }) => ({ ...card, url: brokenImage && card.order === 1 ? "/missing-publication-image.png" : card.url, sha256: "test" })) },
        can_confirm: ready, blockers: ready ? [] : ["请先批准当前版本"], post_url: null, last_error: null, metrics: [] };
    }
    if (saved) { saved.can_confirm = ready; saved.blockers = ready ? [] : ["请先批准当前版本"]; }
    await route.fulfill({ json: saved });
  });
  await page.route("**/api/publication-requests/test-publication/confirm", async route => {
    confirmCount++;
    expect(route.request().postDataJSON().expected_digest).toBe(saved!.digest);
    if (confirmationMode === "reject") { await route.fulfill({ status: 409, json: { error: { message: "发布预览已变化，请重新检查", code: "stale_preview" } } }); return; }
    if (confirmationMode === "draft-network-error") { await route.abort("failed"); return; }
    sendCount++;
    saved!.status = "unknown"; saved!.can_confirm = false;
    await route.abort("failed");
  });
  await page.route("**/api/publication-requests/test-publication/reconcile", async route => {
    expect(route.request().postDataJSON().post_url).toBe("https://www.xiaohongshu.com/explore/test-only");
    saved!.status = "published"; saved!.post_url = "https://www.xiaohongshu.com/explore/test-only";
    await route.fulfill({ json: saved });
  });
  await page.route("**/api/publication-requests/test-publication/metrics", async route => {
    saved!.metrics = [{ likes: 3, favorites: 2, comments: 1, shares: 0, views: null, measured_at: "2026-10-16T10:00:00Z" }];
    await route.fulfill({ json: saved });
  });
  await page.goto(`/runs/${run.id}?publish=1`);
  const panel = page.getByRole("region", { name: "小红书发布" });
  await panel.getByLabel("标题", { exact: true }).fill("同是害怕，这4个英语词怎么选？");
  await panel.getByRole("button", { name: "准备发布单", exact: true }).click();
  await expect(panel.getByRole("button", { name: "确认发布…", exact: true })).toBeDisabled();
  expect(sendCount).toBe(0);
  ready = true;
  await page.getByRole("button", { name: "批准第 1 版" }).click();
  await expect(panel.getByRole("button", { name: "确认发布…", exact: true })).toBeEnabled();
  await panel.getByLabel("标题", { exact: true }).fill("害怕的四种表达");
  await expect(panel.getByRole("button", { name: "确认发布…", exact: true })).toBeDisabled();
  await panel.getByRole("button", { name: "保存修改", exact: true }).click();
  await panel.getByRole("button", { name: "确认发布…", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "确认发布到小红书" });
  await expect(dialog.getByRole("alert")).toContainText("图片读取失败");
  await expect(dialog.getByRole("button", { name: "确认并发布", exact: true })).toBeDisabled();
  expect(confirmCount).toBe(0);
  await dialog.getByRole("button", { name: "返回修改", exact: true }).click();
  brokenImage = false;
  await panel.getByLabel("标题", { exact: true }).fill("害怕的四种表达（修订）");
  await panel.getByRole("button", { name: "保存修改", exact: true }).click();
  await panel.getByRole("button", { name: "确认发布…", exact: true }).click();
  await expect(dialog).toContainText("测试账号（不会发布）");
  await expect(dialog).toContainText("害怕的四种表达");
  await expect(dialog.getByRole("img")).toHaveCount(3);
  await expect(dialog.getByRole("button", { name: "确认并发布", exact: true })).toBeEnabled();
  expect(sendCount).toBe(0);
  await page.screenshot({ path: info.outputPath("publication-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("publication-mobile.png") });
  await dialog.getByRole("button", { name: "返回修改", exact: true }).click();
  await panel.getByRole("button", { name: "确认发布…", exact: true }).click();
  await dialog.getByRole("button", { name: "确认并发布", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText("发布预览已变化");
  await expect(panel.getByLabel("标题", { exact: true })).toBeEnabled();
  await expect(panel.getByRole("button", { name: "核对发布结果", exact: true })).toHaveCount(0);
  expect(sendCount).toBe(0);
  confirmationMode = "draft-network-error";
  await panel.getByRole("button", { name: "确认发布…", exact: true }).click();
  await dialog.getByRole("button", { name: "确认并发布", exact: true }).click();
  await expect(panel.getByRole("button", { name: "核对提交状态", exact: true })).toBeVisible();
  await expect(panel.getByLabel("标题", { exact: true })).toBeDisabled();
  await panel.getByRole("button", { name: "核对提交状态", exact: true }).click();
  await expect(panel.getByLabel("标题", { exact: true })).toBeEnabled();
  expect(sendCount).toBe(0);
  confirmationMode = "unknown";
  await panel.getByRole("button", { name: "确认发布…", exact: true }).click();
  await dialog.getByRole("button", { name: "确认并发布", exact: true }).click();
  await expect(panel.getByRole("button", { name: "核对发布结果", exact: true })).toBeVisible();
  await expect(panel.getByRole("button", { name: "确认发布…", exact: true })).toHaveCount(0);
  expect(sendCount).toBe(1);
  await page.reload();
  await expect(panel.getByRole("button", { name: "核对发布结果", exact: true })).toBeVisible();
  expect(sendCount).toBe(1);
  await panel.getByLabel("笔记链接（可选）").fill("https://www.xiaohongshu.com/explore/test-only");
  await panel.getByRole("button", { name: "核对发布结果", exact: true }).click();
  await expect(panel.getByRole("link", { name: "打开笔记" })).toBeVisible();
  await panel.getByRole("button", { name: "刷新数据", exact: true }).click();
  await expect(panel.locator(".publication-metrics")).toContainText("点赞3");
  await expect(panel.locator(".publication-metrics")).toContainText("阅读—");
  expect(sendCount).toBe(1);
});
