import { expect, test } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

test("original frontend discusses the real eight-image work without account-wide lookup", async ({ page }, info) => {
  const root = path.resolve(process.env.CREATOROS_CONTEXT_FOCUS_ROOT!);
  const setup = JSON.parse(readFileSync(path.join(root, "setup.json"), "utf8"));
  const writes: { url: string; body: Record<string, unknown> }[] = [];
  page.on("request", req => {
    if (req.method() !== "GET") writes.push({ url: new URL(req.url()).pathname, body: req.postDataJSON() });
  });
  await page.goto(`/?creator=${setup.creator_id}&series=${setup.series_id}`);
  const card = page.getByTestId(`library-${setup.topic_id}`);
  await card.locator("summary").click();
  await card.getByRole("button", { name: "详情与编辑", exact: true }).click();
  await page.getByRole("dialog", { name: "选题详情" }).getByRole("button", { name: "与 Agent 讨论", exact: true }).click();
  const panel = page.locator(".account-chat-panel");
  await expect(panel).toBeVisible();
  await expect(panel.locator(".agent-context-focus")).toContainText(setup.title);
  expect(writes).toEqual([]);
  const query = `我们讨论一下选题「${setup.title}」。我觉得这一组内容不错，但是画面整体太明亮，没有 Skill 参考图那么简约。请直接比较这篇已生成的八张图与当前呈现 Skill 参考图，指出背景和物品上的具体差异。只分析，不修改、不生产、不启动校准。`;
  await panel.getByLabel("给 Agent 的消息").fill(query);
  const accepted = page.waitForResponse(res => res.request().method() === "POST" && /\/turns$/.test(new URL(res.url()).pathname));
  await panel.getByRole("button", { name: "发送 ↑", exact: true }).click();
  const response = await accepted;
  expect(response.status(), await response.text()).toBe(202);
  const doc = await response.json();
  const submitted = writes.find(item => item.url.endsWith("/turns"))!.body;
  expect(submitted.context_target).toEqual({ series_id: setup.series_id, topic_id: setup.topic_id, run_id: setup.run_id });
  await expect(panel.locator(".chat-reply-actions")).toHaveCount(1, { timeout: 180_000 });
  await expect(panel.locator(".agent-compose-status")).not.toContainText("正在");
  const final = JSON.parse(readFileSync(path.join(root, "chats", doc.id, "view.json"), "utf8"));
  expect(final.status).toBe("idle");
  expect(final.error).toBeNull();
  const reply = panel.locator(".chat-answer").last();
  await expect(reply).not.toBeEmpty();
  await expect(reply).toContainText(/背景|留白|道具|物品/);
  const toolCalls = final.entries.filter((entry: { kind: string }) => entry.kind === "tool");
  expect(toolCalls.map((entry: { name: string }) => entry.name)).not.toContain("get_creator_tasks");
  expect(toolCalls.map((entry: { name: string }) => entry.name)).not.toContain("list_creator_series");
  const traceLines = readFileSync(path.join(root, "chats", doc.id, "messages.context-trace.jsonl"), "utf8").trim().split(/\r?\n/).map(line => JSON.parse(line));
  const requests = traceLines.filter(row => row.event === "finished");
  const snapshots = [...new Set<string>(traceLines.map(row => row.request_id).filter(Boolean))].map(id =>
    JSON.parse(readFileSync(path.join(root, "chats", doc.id, "messages.request-trace", `${id}.json`), "utf8")));
  expect(snapshots.length).toBeGreaterThan(0);
  const first = snapshots[0];
  const outgoing = first.context.messages;
  expect(Array.isArray(outgoing)).toBe(true);
  const focus = outgoing.find((message: { content: unknown }) => Array.isArray(message.content)
    && message.content.some((block: { text?: string }) => block.text?.startsWith("[本问选定作品的资料")));
  expect(focus).toBeTruthy();
  const material = JSON.parse(focus.content[0].text.split("\n").slice(1).join("\n"));
  expect(material.target.topic_id).toBe(setup.topic_id);
  expect(material.image_inventory.filter((item: { kind: string; loaded: boolean }) => item.kind === "production" && item.loaded)).toHaveLength(8);
  expect(material.image_inventory.some((item: { kind: string; loaded: boolean }) => item.kind === "skill_reference" && item.loaded)).toBe(true);
  expect(focus.content.filter((block: { type: string }) => block.type === "image_ref")).toHaveLength(material.loaded_image_count);
  expect(first.context.tools).toHaveLength(31);
  for (const snapshot of snapshots) expect(snapshot.context.tools).toEqual(first.context.tools);
  const ledger = readFileSync(path.join(root, "chats", doc.id, "messages.json"), "utf8");
  expect(ledger).not.toContain("本问选定作品的资料");
  expect(ledger).toContain(setup.topic_id);
  await page.screenshot({ path: info.outputPath("real-focus-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("real-focus-mobile.png"), fullPage: true });
  await page.reload();
  await expect(page.locator(".chat-answer").last()).toHaveText(await reply.textContent() ?? "");
  expect(writes.map(item => item.url)).toEqual(["/api/agent/sessions", `/api/agent/sessions/${doc.id}/turns`]);
  const usage = final.entries.filter((entry: { kind: string }) => entry.kind === "usage");
  writeFileSync(path.join(root, "frontend-result.json"), JSON.stringify({
    session_id: doc.id, request_id: submitted.request_id, query, target: submitted.context_target,
    reply: final.entries.filter((entry: { kind: string }) => entry.kind === "assistant").map((entry: { text: string }) => entry.text).join("\n"),
    tools: toolCalls.map((entry: { name: string }) => entry.name), usage, trace_finished_rows: requests.length,
    model_requests: snapshots.length, loaded_images: material.loaded_image_count,
    image_inventory: material.image_inventory, omissions: material.omissions, browser_writes: writes,
  }, null, 2));
});
