import { expect, test } from "@playwright/test";

test("closing the operation drawer stops its overview observer", async ({ page }) => {
  let overviewRequests = 0;
  await page.route("**/api/overview", async route => {
    overviewRequests += 1;
    await route.fulfill({ json: {
      counts: { creator_count: 0, active_creator_count: 0, series_count: 0, active_series_count: 0, producing_count: 0, awaiting_approval_count: 0 },
      creators: [], needs_attention: [], producing: [], awaiting_approval: [], pending_operations: [],
    } });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "运营指令" }).click();
  await expect(page.getByRole("dialog", { name: "运营指令" })).toBeVisible();
  await expect.poll(() => overviewRequests).toBe(1);

  await page.getByRole("button", { name: "关闭运营指令" }).click();
  await expect(page.getByRole("dialog", { name: "运营指令" })).toBeHidden();
  await page.waitForTimeout(11_000);

  expect(overviewRequests).toBe(1);
});

test("a terminal run keeps its history but does not poll run or event endpoints", async ({ page }) => {
  const runId = "terminal-polling-e2e";
  const requests = { run: 0, events: 0, stream: 0 };
  await page.route(`**/api/runs/${runId}**`, async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/events/stream")) {
      requests.stream += 1;
      await route.fulfill({ status: 200, contentType: "text/event-stream", body: "event: snapshot\ndata: {}\n\n" });
    } else if (url.pathname.endsWith("/events")) {
      requests.events += 1;
      await route.fulfill({ json: { items: [{ id: 4, run_id: runId, revision_id: null, attempt_id: null,
        event_type: "interrupted", from_status: "producing", to_status: "interrupted", created_at: "2026-10-05T00:00:00Z" }], next_after_id: 4 } });
    } else {
      requests.run += 1;
      await route.fulfill({ json: {
        id: runId, creator_id: "creator-e2e", creator_name: "隔离测试账号", series_id: "series-e2e", series_name: "隔离测试栏目",
        topic_id: "topic-e2e", topic_title: "已结束运行", status: "interrupted", version: 2, active_revision_number: 1,
        updated_at: "2026-10-05T00:00:00Z", completed_at: null, heartbeat_at: null, lease_expires_at: null,
        retryable: true, error_stage: "producing", error_type: "interrupted", error_message: "等待显式恢复。",
        allowed_actions: ["view", "resume"], cover_url: null, card_count: null, input_snapshot: {}, producer_thread_id: null,
        events_url: `/api/runs/${runId}/events`, revisions: [], partial_cards: [], publication: null,
      } });
    }
  });

  await page.goto(`/runs/${runId}`);
  await expect(page.getByRole("heading", { name: "已结束运行" })).toBeVisible();
  await page.getByRole("button", { name: /^生产记录 ·/ }).click();
  await expect(page.getByText("执行中断")).toBeVisible();
  expect(requests).toEqual({ run: 1, events: 1, stream: 0 });

  await page.waitForTimeout(11_000);

  expect(requests).toEqual({ run: 1, events: 1, stream: 0 });
});

test("idle chat and closed floating panel do not keep fetching sessions", async ({ page, request }) => {
  const creatorResponse = await request.post("/api/creators", { data: { display_name: "空闲会话轮询验收" } });
  const creator = await creatorResponse.json();
  const sessionResponse = await request.post("/api/agent/sessions", { data: { creator_id: creator.id } });
  const session = await sessionResponse.json();
  expect(creatorResponse.status()).toBe(201);
  expect(sessionResponse.status()).toBe(201);
  const counts = { detail: 0, list: 0, stream: 0 };
  page.on("request", event => {
    const path = new URL(event.url()).pathname;
    if (path === `/api/agent/sessions/${session.id}`) counts.detail++;
    if (path === "/api/agent/sessions") counts.list++;
    if (path === `/api/agent/sessions/${session.id}/events`) counts.stream++;
  });
  await page.goto(`/agent?creator=${creator.id}&chat=${session.id}`);
  await expect(page.locator(".agent-history-list button")).toHaveCount(1);
  await expect(page.getByText("记录已保存")).toBeVisible();
  expect(counts).toEqual({ detail: 1, list: 1, stream: 0 });
  await page.waitForTimeout(11_000);
  expect(counts).toEqual({ detail: 1, list: 1, stream: 0 });

  await page.goto(`/?creator=${creator.id}`);
  await page.getByRole("button", { name: "打开账号对话" }).click();
  await expect(page.locator(".account-chat-panel")).toBeVisible();
  await page.getByRole("button", { name: "关闭账号对话" }).click();
  await expect(page.locator(".account-chat-panel")).toBeHidden();
  const closed = { ...counts };
  await page.waitForTimeout(11_000);
  expect(counts).toEqual(closed);
});
