import { expect, test } from "@playwright/test";
import type { AccountAnalyticsView } from "../src/api/analytics";

test("account data reads SQLite projection, persists settings and handles sparse samples without publishing", async ({ page, request }) => {
  // Isolated real Studio account/navigation; analytics responses are controlled
  // fixtures for UI edge cases, not evidence of real platform feedback.
  const first = await request.post("/api/creators", { data: { display_name: "数据看板隔离账号" } }).then(r => r.json());
  const second = await request.post("/api/creators", { data: { display_name: "空数据隔离账号" } }).then(r => r.json());
  const series = await request.post(`/api/creators/${first.id}/series`, { data: { name: "趋势测试栏目" } }).then(r => r.json());
  const data: AccountAnalyticsView = {
    creator_id: first.id, enabled: false, checking: false, checked_at: null, last_updated_at: "2026-10-16T12:00:00Z", next_check_at: "2026-10-16T12:05:00Z",
    heartbeat_seconds: 300, refresh_seconds: 1800, window_days: 7, error: null,
    summary: { notes: 1, sampled_notes: 1, likes: 3, favorites: 2, comments: 0, views: null, shares: null },
    notes: [{ request_id: "test-note", run_id: "test-run", series_id: series.id, series_name: series.name,
      title: "同是害怕，这4个英语词怎么选？", post_url: "https://www.xiaohongshu.com/explore/fixture-only", published_at: "2026-10-16T09:00:00Z",
      last_updated_at: "2026-10-16T12:00:00Z", next_refresh_at: "2026-10-16T12:30:00Z", error: null,
      samples: [{ measured_at: "2026-10-16T10:00:00Z", likes: 4, favorites: 2, comments: null, views: null, shares: null },
        { measured_at: "2026-10-16T10:30:00Z", likes: null, favorites: 2, comments: null, views: null, shares: null },
        { measured_at: "2026-10-16T12:00:00Z", likes: 3, favorites: 2, comments: 0, views: null, shares: null }] }],
  };
  const empty = { ...data, creator_id: second.id, enabled: false, notes: [], last_updated_at: null,
    summary: { notes: 0, sampled_notes: 0, likes: null, favorites: null, comments: null, views: null, shares: null } };
  let refreshCount = 0;
  let settingsCount = 0;
  let forbiddenCount = 0;
  page.on("request", req => { if (/\/confirm$|\/publication-request$/.test(new URL(req.url()).pathname) && req.method() === "POST") forbiddenCount++; });
  await page.route(`**/api/creators/${first.id}/analytics**`, async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/settings")) { settingsCount++; data.enabled = route.request().postDataJSON().enabled; }
    if (path.endsWith("/refresh")) { refreshCount++; data.checking = true; }
    await route.fulfill({ json: data });
  });
  await page.route(`**/api/creators/${second.id}/analytics**`, route => route.fulfill({ json: empty }));
  await page.goto(`/?creator=${first.id}`);
  await page.getByRole("navigation", { name: "账号工作区" }).getByRole("button", { name: "数据", exact: true }).click();
  await expect(page).toHaveURL(/view=data/);
  const panel = page.getByRole("region", { name: "账号数据", exact: true });
  await expect(panel).toContainText("最近 7 天发布");
  await expect(panel.getByRole("switch", { name: "自动更新" })).not.toBeChecked();
  await panel.getByRole("switch", { name: "自动更新" }).click();
  await expect(panel.getByRole("switch", { name: "自动更新" })).toBeChecked();
  expect(settingsCount).toBe(1);
  await page.reload();
  await expect(panel.getByRole("switch", { name: "自动更新" })).toBeChecked();
  await expect(panel.locator(".analytics-report")).toContainText("减少 1");
  await expect(panel.locator(".analytics-chart-line")).toHaveCount(2); // Missing middle sample is not connected.
  await panel.getByRole("group", { name: "趋势指标" }).getByRole("button", { name: "阅读", exact: true }).click();
  await expect(panel).toContainText("暂无阅读数据");
  await expect(panel.locator("svg.analytics-chart")).toHaveCount(0);
  await panel.getByRole("group", { name: "趋势指标" }).getByRole("button", { name: "评论", exact: true }).click();
  await expect(panel.locator(".analytics-chart-point")).toHaveCount(1);
  await expect(panel.locator(".analytics-report")).toContainText("目前只有一次采样");
  await panel.getByRole("button", { name: "刷新数据", exact: true }).click();
  await expect(panel.getByRole("button", { name: "更新中…", exact: true })).toBeDisabled();
  expect(refreshCount).toBe(1);
  data.checking = false; data.error = "平台暂不可用，保留上次数据。";
  await expect(panel.getByRole("alert")).toContainText("保留上次数据");
  await expect(panel.locator(".analytics-chart-point")).toHaveCount(1);
  await page.getByRole("button", { name: `查看账号 ${second.display_name}` }).click();
  await expect(panel).toContainText("还没有近期发布的笔记");
  await expect(panel.getByRole("switch", { name: "自动更新" })).not.toBeChecked();
  await expect(panel.getByRole("button", { name: "刷新数据", exact: true })).toBeDisabled();
  await expect(panel).not.toContainText(data.notes[0].title);
  await page.getByRole("button", { name: "趋势测试栏目", exact: true }).click();
  await expect(page).not.toHaveURL(/view=data/);
  await expect(page.getByRole("heading", { name: "趋势测试栏目", exact: true })).toBeVisible();
  await page.goto(`/?creator=${first.id}&view=data`);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(panel.getByRole("switch", { name: "自动更新" })).toBeVisible();
  await expect.poll(() => panel.locator(".analytics-chart text").evaluateAll(elements => elements.every(element => {
    const bounds = element.getBoundingClientRect();
    return bounds.left >= 0 && bounds.right <= innerWidth && bounds.height >= 10;
  }))).toBe(true);
  await expect(panel.locator(".analytics-trend-heading label")).toHaveCSS("white-space", "nowrap");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(forbiddenCount).toBe(0);
});
