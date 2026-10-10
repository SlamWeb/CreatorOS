import { expect, test } from "@playwright/test";

test("reply copy and trace stay on the selected session and load one snapshot at a time", async ({ page, context }, info) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const longPrompt = `developer context\n${"x".repeat(620)}`;
  const sessions = [
    { id: "trace-one", title: "Trace 检查", version: 1, status: "idle", error: null, scope_kind: "overview", creator_id: null,
      entries: [
        { kind: "assistant", text: "工具调用中的说明。", complete: false, terminal: false, turn_id: "turn-in-progress" },
        { kind: "assistant", text: "**最终回复**\n\n原始 Markdown。", complete: true, terminal: true, turn_id: "turn-one", model_request_id: "request-one" },
      ], updated_at: "2026-10-05T08:00:00Z", has_older: false },
    { id: "trace-two", title: "旧回复", version: 1, status: "idle", error: null, scope_kind: "overview", creator_id: null,
      entries: [{ kind: "assistant", text: "没有 trace id 的历史回复。" }], updated_at: "2026-10-04T08:00:00Z", has_older: false },
  ];
  const snapshotsRead: string[] = [];
  const snapshotAttempts: string[] = [];
  let failFirstSnapshot = true;
  await page.route("**/api/agent/sessions**", async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/events")) return route.abort();
    if (url.pathname.includes("/turn-trace/") && !url.pathname.includes("/requests/")) {
      return route.fulfill({ json: { turn_id: "turn-one", status: "complete", available: true, requests: [
        { request_id: "request-one", request_kind: "main", event: "finished", status: "complete", sent: true, model: "test-model", estimated_input_tokens: 123, estimated_parts: { messages: 90 }, usage: { input_tokens: 110, output_tokens: 18 }, input_limit: 8192, finish_reason: "stop", elapsed_ms: 310, snapshot_available: true },
        { request_id: "request-two", request_kind: "compaction", event: "finished", status: "complete", sent: true, model: "test-model", estimated_input_tokens: 456, usage: { input_tokens: 400, output_tokens: 21 }, compacted: true, externalized: true, snapshot_available: true },
      ] } });
    }
    if (url.pathname.includes("/requests/")) {
      const requestId = url.pathname.split("/").at(-1)!;
      snapshotAttempts.push(requestId);
      if (requestId === "request-one" && failFirstSnapshot) {
        failFirstSnapshot = false;
        return route.fulfill({ status: 503, json: { error: { message: "受控快照故障" } } });
      }
      snapshotsRead.push(requestId);
      return route.fulfill({ json: { schema_version: 1, turn_id: "turn-one", request_id: requestId,
        context: { messages: [{ role: "developer", content: longPrompt }, { role: "assistant", content: null, tool_calls: [{ id: "call-history", function: { name: "list_series", arguments: "{}" } }] }, { role: "tool", tool_call_id: "call-history", content: "历史工具回包" }, { role: "user", content: "请检查上下文" }], tools: [{ name: "list_series", parameters: { type: "object" } }], max_output_tokens: requestId === "request-two" ? null : 1200 },
        response: { role: "assistant", content: "已完成", tool_calls: [
          { id: "call-1", name: "list_series", arguments: { creator_id: "creator-1" } },
          { id: "call-2", name: "list_series", arguments: { creator_id: "creator-2" } },
        ] },
        tool_results: [
          { tool_call_id: "call-1", name: "list_series", content: "结果文本", raw_content: "结果文本 · 原始内部路径和技术回执", is_error: false },
          { tool_call_id: "call-orphan", name: "old_tool", content: "孤立返回仍保留", is_error: false },
        ], redacted: false } });
    }
    if (url.pathname.endsWith("/trace-one")) return route.fulfill({ json: sessions[0] });
    if (url.pathname.endsWith("/trace-two")) return route.fulfill({ json: sessions[1] });
    return route.fulfill({ json: { items: sessions } });
  });

  await page.goto("/agent?chat=trace-one");
  const intermediate = page.getByText("工具调用中的说明。").locator("xpath=ancestor::div[contains(@class,'chat-reply')]");
  await expect(intermediate.locator(".chat-reply-actions")).toHaveCount(0);
  const reply = page.getByText("最终回复").locator("xpath=ancestor::div[contains(@class,'chat-reply')]");
  await reply.getByRole("button", { name: "复制回复原文" }).click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText().then(text => text.replace(/\r\n/g, "\n"))))
    .toBe("**最终回复**\n\n原始 Markdown。");
  await reply.getByRole("button", { name: "查看回复 Trace" }).click();
  const dialog = page.getByRole("dialog", { name: "回复 Trace" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("alert")).toContainText("受控快照故障");
  await dialog.getByRole("button", { name: "重试" }).click();
  await expect.poll(() => snapshotsRead).toEqual(["request-one"]);
  await expect(dialog.getByRole("heading", { name: "System / Developer" })).toBeVisible();
  await expect(dialog.getByText("历史工具调用 · 1")).toBeVisible();
  await expect(dialog.getByRole("tab", { name: "上下文" })).toHaveAttribute("aria-selected", "true");
  const metadata = dialog.locator(".chat-trace-metadata");
  await expect(metadata).not.toHaveAttribute("open", "");
  await expect(metadata.locator("summary")).toContainText("test-model · 输入 110 · 输出 18");
  const developerText = dialog.locator(".chat-trace-reading-message .chat-trace-block code").first();
  await expect(developerText).toContainText(/^developer context\nx{100,}/);
  await dialog.getByRole("button", { name: "展开全文" }).click();
  await expect(developerText).toHaveText(longPrompt);
  await dialog.getByRole("button", { name: "原始消息" }).click();
  await expect(dialog.getByRole("button", { name: "原始消息" })).toHaveAttribute("aria-pressed", "true");
  await expect(dialog.getByText(/"tool_calls"/).first()).toBeVisible();
  await dialog.getByRole("button", { name: "阅读", exact: true }).click();
  await expect(dialog.getByText("历史工具调用 · 1")).toBeVisible();
  await expect(dialog.getByText("tool · call ID call-history")).toBeVisible();
  await metadata.locator("summary").click();
  await expect(metadata).toHaveAttribute("open", "");
  await expect(metadata.getByText("估算组成")).toBeVisible();
  await expect(metadata.getByText("结束原因")).toBeVisible();
  await page.screenshot({ path: info.outputPath("reply-trace-desktop.png") });
  await dialog.getByRole("tab", { name: "模型输出" }).click();
  await expect(dialog.getByRole("tabpanel").getByText("已完成")).toBeVisible();
  await dialog.getByRole("tab", { name: "工具" }).click();
  await expect(dialog.getByRole("tabpanel").getByText("结果文本")).toBeVisible();
  await expect(dialog.getByText("结果文本 · 原始内部路径和技术回执", { exact: true })).toHaveCount(0);
  await dialog.getByRole("button", { name: "查看原始返回" }).click();
  await expect(dialog.getByRole("button", { name: "收起原始返回" })).toHaveAttribute("aria-expanded", "true");
  await expect(dialog.getByText("结果文本 · 原始内部路径和技术回执", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "收起原始返回" }).click();
  await expect(dialog.getByText("结果文本 · 原始内部路径和技术回执", { exact: true })).toHaveCount(0);
  await expect(dialog.getByText("未记录结果；执行结果未知。", { exact: true })).toBeVisible();
  await expect(dialog.getByText("孤立返回仍保留")).toBeVisible();
  await dialog.getByRole("button", { name: /步骤 2 · 上下文压缩/ }).click();
  await expect.poll(() => snapshotsRead).toEqual(["request-one", "request-two"]);
  await expect(dialog.getByRole("tab", { name: "上下文" })).toHaveAttribute("aria-selected", "true");
  await expect(dialog.getByText("模型默认")).toBeVisible();
  await metadata.locator("summary").click();
  await expect(dialog.getByText("已压缩 · 已外置")).toBeVisible();
  await metadata.locator("summary").click();
  await expect(dialog.getByRole("button", { name: "展开全文" })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(dialog.getByRole("tab", { name: "上下文" })).toBeVisible();
  await page.screenshot({ path: info.outputPath("reply-trace-mobile.png") });
  await dialog.getByRole("button", { name: "展开全文" }).click();
  await expect(dialog.getByRole("button", { name: "收起" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(reply.getByRole("button", { name: "查看回复 Trace" })).toBeFocused();

  await reply.getByRole("button", { name: "查看回复 Trace" }).click();
  await expect(dialog).toBeVisible();
  await page.goto("/agent?chat=trace-two");
  await expect(dialog).toBeHidden();
  await expect(page.getByText("没有 trace id 的历史回复。")).toBeVisible();
  await page.getByRole("button", { name: "历史对话", exact: true }).click();
  await page.getByRole("button", { name: "旧回复" }).click();
  const oldReply = page.getByText("没有 trace id 的历史回复。").locator("xpath=ancestor::div[contains(@class,'chat-reply')]");
  await oldReply.getByRole("button", { name: "查看回复 Trace" }).click();
  await expect(page.getByText("这条旧记录没有保存请求标识，无法关联到 Trace 快照。", { exact: true })).toBeVisible();
  expect(snapshotsRead).toEqual(["request-one", "request-two"]);
  expect(snapshotAttempts).toEqual(["request-one", "request-one", "request-two"]);
});
