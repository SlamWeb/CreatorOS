import { expect, test, type Page } from "@playwright/test";

const runId = "production-progress-e2e";
const now = Date.now();

function runProjection(progress: unknown, overrides: Record<string, unknown> = {}) {
  return {
    id: runId,
    creator_id: "creator-e2e",
    creator_name: "隔离测试账号",
    series_id: "series-e2e",
    series_name: "隔离测试栏目",
    topic_id: "topic-e2e",
    topic_title: "阶段进度验收选题",
    status: "producing",
    version: 1,
    active_revision_number: 1,
    updated_at: new Date(now).toISOString(),
    completed_at: null,
    heartbeat_at: new Date(now).toISOString(),
    lease_expires_at: new Date(now + 60_000).toISOString(),
    retryable: false,
    error_stage: null,
    error_type: null,
    error_message: null,
    allowed_actions: ["view"],
    cover_url: null,
    card_count: null,
    input_snapshot: {},
    producer_thread_id: "thread-e2e",
    events_url: `/api/runs/${runId}/events`,
    publication: null,
    production_progress: progress,
    revisions: [{
      id: "revision-e2e",
      revision_number: 1,
      instruction: null,
      artifact_available: false,
      artifact_digest: null,
      validation: null,
      validated_at: null,
      approved_at: null,
      attempts: [{
        id: "attempt-e2e",
        attempt_number: 1,
        status: "running",
        producer_thread_id: "thread-e2e",
        has_output: false,
        usage: null,
        trace_available: false,
        error_type: null,
        error_message: null,
        started_at: new Date(now - 180_000).toISOString(),
        heartbeat_at: new Date(now).toISOString(),
        completed_at: null,
        duration_ms: null,
      }],
      artifact_error: null,
      content_summary: null,
      review_digest: null,
      cards: [],
      publish_copy: null,
      sources: [],
    }],
    ...overrides,
  };
}

function progress(stage: "mind" | "visual" | "production", activity: string, ageSeconds = 18) {
  return {
    stage,
    status: "running",
    started_at: new Date(now - 300_000).toISOString(),
    last_activity_at: new Date(now - ageSeconds * 1_000).toISOString(),
    last_event: "item.started",
    activity,
    completed_tool_calls: 3,
    total_pages: stage === "visual" ? 11 : null,
    phase: stage === "visual" ? "rendering" : null,
    current_page: stage === "visual" ? 2 : null,
    completed_pages: stage === "visual" ? 1 : 0,
    page_attempt: stage === "visual" ? 2 : 0,
  };
}

async function openProjection(page: Page, projection: ReturnType<typeof runProjection>) {
  let getCount = 0;
  let postCount = 0;
  const imageRequests: string[] = [];
  await page.route(`**/api/runs/${runId}**`, async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (request.method() !== "GET") postCount += 1;
    if (pathname.includes("/partial-cards/") || pathname.includes("/cards/")) {
      imageRequests.push(request.url());
      await route.fulfill({ status: 200, contentType: "image/png", body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/pC8AAAAASUVORK5CYII=", "base64") });
    } else if (pathname.endsWith("/events/stream")) {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: "event: snapshot\ndata: {}\n\n",
      });
    } else if (pathname.endsWith("/events")) {
      await route.fulfill({ json: { items: [], next_after_id: 0 } });
    } else if (request.method() === "GET" && pathname === `/api/runs/${runId}`) {
      getCount += 1;
      await route.fulfill({ json: projection });
    } else {
      await route.fulfill({ status: 204, body: "" });
    }
  });
  await page.goto(`/runs/${runId}`);
  await expect(page.getByRole("heading", { name: "阶段进度验收选题" })).toBeVisible();
  return {
    reads: () => getCount,
    writes: () => postCount,
    imageRequests: () => imageRequests,
  };
}

async function verifyDesktopAndMobile(page: Page, testInfo: { outputPath: (name: string) => string }, name: string) {
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`${name}-${viewport.width}.png`), fullPage: true });
  }
}

test("shows mind research stage, real activity, page count, and stays read-only on refresh", async ({ page }, testInfo) => {
  const requests = await openProjection(page, runProjection(progress("mind", "searching")));
  await expect(page.getByText("内容调研 · 进行中")).toBeVisible();
  await expect(page.getByText(/搜索中 · 最近活动/)).toBeVisible();
  await expect(page.getByText("共 11 页")).toHaveCount(0);
  await expect(page.getByText("共 5 页")).toHaveCount(0);
  await expect(page.getByText("已完成工具调用 3 次")).toBeVisible();
  await expect(page.getByRole("button", { name: /开始生产|恢复生产/ })).toHaveCount(0);
  await expect(page.locator(".stream-status")).toContainText("状态");
  await page.reload();
  await expect(page.getByText("内容调研 · 进行中")).toBeVisible();
  expect(requests.reads()).toBeGreaterThanOrEqual(2);
  expect(requests.writes()).toBe(0);
  await verifyDesktopAndMobile(page, testInfo, "mind-progress");
});

test("shows visual production without inventing page counts", async ({ page }, testInfo) => {
  await openProjection(page, runProjection(progress("visual", "tool_running")));
  await expect(page.getByText("图片制作 · 进行中")).toBeVisible();
  await expect(page.getByText(/工具执行中 · 最近活动/)).toBeVisible();
  await expect(page.getByText("共 11 页")).toBeVisible();
  await expect(page.getByText("逐页制图 · 第 2 页 · 第 2 次尝试 · 已完成 1 页")).toBeVisible();
  await verifyDesktopAndMobile(page, testInfo, "visual-progress");
});

test("shows checksum-served partial pages without enabling approval", async ({ page }, testInfo) => {
  const checksum1 = "a".repeat(64);
  const checksum2 = "b".repeat(64);
  const projection = runProjection(progress("visual", "tool_running"), {
    partial_cards: [
      { order: 2, image_url: `/api/runs/${runId}/partial-cards/2?checksum=${checksum2}`, warnings: ["小字对比度需要人工检查"] },
      { order: 1, image_url: `/api/runs/${runId}/partial-cards/1?checksum=${checksum1}`, warnings: [] },
    ],
    revisions: [{
      id: "revision-e2e", revision_number: 1, instruction: null, artifact_available: false,
      artifact_digest: null, validation: null, validated_at: null, approved_at: null,
      attempts: [{ id: "attempt-e2e", attempt_number: 1, status: "running", producer_thread_id: "thread-e2e",
        has_output: false, usage: null, trace_available: false, error_type: null, error_message: null,
        started_at: new Date(now - 180_000).toISOString(), heartbeat_at: new Date(now).toISOString(),
        completed_at: null, duration_ms: null }],
      artifact_error: null, content_summary: null, review_digest: null,
      cards: [],
      publish_copy: null, sources: [],
    }],
  });
  const requests = await openProjection(page, projection);
  await expect(page.getByText("最终产物", { exact: true })).toHaveCount(0);
  await expect(page.getByText("制作中预览 · 尚未验收", { exact: true })).toBeVisible();
  await expect(page.getByRole("img", { name: "第 1 页制作中预览，尚未验收" })).toBeVisible();
  await expect(page.getByText("小字对比度需要人工检查")).toBeVisible();
  await expect(page.locator(".partial-preview-card figcaption")).toHaveText(["第 1 页 · 尚未验收", "第 2 页 · 尚未验收"]);
  await expect(page.getByRole("button", { name: /批准/ })).toHaveCount(0);
  await expect.poll(() => requests.imageRequests().filter((url) => url.includes("/partial-cards/")).length).toBe(2);
  expect(requests.imageRequests().filter((url) => url.includes("/partial-cards/")).every((url) => new URL(url).searchParams.has("checksum"))).toBe(true);
  await page.reload();
  await expect(page.getByText("制作中预览 · 尚未验收", { exact: true })).toBeVisible();
  expect(requests.writes()).toBe(0);
  await verifyDesktopAndMobile(page, testInfo, "partial-pages");
});

test("final verified cards replace the unreviewed preview instead of duplicating it", async ({ page }) => {
  const checksum = "c".repeat(64);
  const base = runProjection(progress("visual", "completed"));
  await openProjection(page, runProjection(progress("visual", "completed"), {
    status: "awaiting_approval",
    partial_cards: [{ order: 1, image_url: `/api/runs/${runId}/partial-cards/1?checksum=${checksum}`, warnings: [] }],
    revisions: [{ ...base.revisions[0],
      cards: [{ order: 1, headline: "最终卡片样例", url: `/api/runs/${runId}/cards/1?checksum=${checksum}`,
        width: 1, height: 1, page_spec: null, image_prompt: null }] }],
  }));
  await expect(page.getByText("最终产物", { exact: true })).toBeVisible();
  await expect(page.getByText("制作中预览 · 尚未验收", { exact: true })).toHaveCount(0);
});

test("stale stage activity is described as quiet, not as a failure", async ({ page }, testInfo) => {
  await openProjection(page, runProjection(progress("production", "waiting", 180)));
  await expect(page.getByText("生产 · 进行中")).toBeVisible();
  await expect(page.getByText(/暂无新活动 ·/)).toBeVisible();
  await expect(page.getByText("生产 · 阶段失败")).toHaveCount(0);
  await verifyDesktopAndMobile(page, testInfo, "stale-progress");
});

test("shows a failed stage and preserves the server failure cause", async ({ page }, testInfo) => {
  const failed = {
    ...progress("mind", "failed", 180),
    status: "failed",
  };
  await openProjection(page, runProjection(failed, {
    status: "failed",
    heartbeat_at: null,
    lease_expires_at: null,
    retryable: true,
    error_stage: "mind",
    error_type: "ResearchError",
    error_message: "隔离验收：调研来源暂不可用",
    allowed_actions: ["resume", "revise", "cancel"],
  }));
  await expect(page.getByText("内容调研 · 阶段失败")).toBeVisible();
  await expect(page.getByText("隔离验收：调研来源暂不可用")).toBeVisible();
  await expect(page.getByText(/暂无新活动/)).toHaveCount(0);
  await verifyDesktopAndMobile(page, testInfo, "failed-progress");
});

test("a completed production stage still waits for run receipt and validation", async ({ page }, testInfo) => {
  const complete = {
    ...progress("production", "completed"),
    status: "completed",
    activity: "completed",
  };
  await openProjection(page, runProjection(complete));
  await expect(page.getByText("生产 · 正在整理回执/验收")).toBeVisible();
  await expect(page.getByText("生产 · 阶段已完成", { exact: true })).toHaveCount(0);
  await expect(page.getByText("正在整理回执/验收", { exact: true })).toHaveCount(0);
  await verifyDesktopAndMobile(page, testInfo, "receipt-validation");
});

test("legacy run without production progress says progress was not recorded", async ({ page }, testInfo) => {
  await openProjection(page, runProjection(null));
  await expect(page.getByText("未记录阶段进度")).toBeVisible();
  await verifyDesktopAndMobile(page, testInfo, "legacy-progress");
});
