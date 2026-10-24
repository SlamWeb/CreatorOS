import { expect, test, type Page, type APIRequestContext } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

const root = path.resolve(process.env.CREATOROS_MEDIA_AGENT_ROOT!);
const setup = () => JSON.parse(readFileSync(path.join(root, "setup.json"), "utf8"));
async function chat(page: Page, request: APIRequestContext, query: string, filename: string) {
  await page.getByRole("button", { name: "打开账号对话", exact: true }).click();
  const panel = page.locator(".account-chat-panel");
  await panel.getByRole("textbox", { name: "给 Agent 的消息" }).fill(query);
  const accepted = page.waitForResponse(r => r.request().method() === "POST" && /\/turns$/.test(new URL(r.url()).pathname));
  await panel.getByRole("button", { name: "发送 ↑", exact: true }).click();
  const response = await accepted;
  expect(response.status(), await response.text()).toBe(202);
  const submitted = await response.json();
  writeFileSync(path.join(root, filename), JSON.stringify({ query, session_id: submitted.id, phase: "submitted" }, null, 2));
  await expect(panel.locator(".chat-reply-actions")).toHaveCount(1, { timeout: 180_000 });
  const doc = await request.get(`/api/agent/sessions/${submitted.id}`).then(r => r.json());
  writeFileSync(path.join(root, filename), JSON.stringify({ query, ...doc }, null, 2));
  expect(doc.status).toBe("idle"); expect(doc.error).toBeNull();
  return doc;
}

test("ordinary topic research is answered by real DS without delegating Codex", async ({ page, request }) => {
  const fixture = setup();
  await page.goto(`/?creator=${fixture.creator_id}`);
  const doc = await chat(page, request, "帮我给英语词汇栏目调研5组高考考研常用的同义词选题，先给我建议。", "topics-result.json");
  const tools = doc.entries.filter((e: { kind: string }) => e.kind === "tool");
  expect(tools.some((e: { name: string }) => ["research_series_topics", "start_content_run", "manage_skill_iteration"].includes(e.name))).toBe(false);
  const reply = doc.entries.filter((e: { kind: string }) => e.kind === "assistant").map((e: { text: string }) => e.text).join("\n");
  expect(reply).toMatch(/[A-Za-z]{3}/); expect(reply.length).toBeGreaterThan(100);
  const preserved = await request.get("/test/media-fixture").then(r => r.json());
  expect(preserved.workers_started).toBe(false); expect(preserved.source_images_unchanged).toBe(true);
  expect(preserved.source_run_rows_unchanged).toBe(true);
});

test("column name can be edited on Web and through real account DS without changing identity", async ({ page, request }, info) => {
  const fixture = setup();
  await page.goto(`/?creator=${fixture.creator_id}&series=${fixture.test_series_id}`);
  await page.getByRole("button", { name: "栏目设置", exact: true }).click();
  await page.getByRole("textbox", { name: "栏目名称", exact: true }).fill("英语词汇实验室");
  await page.getByRole("button", { name: "保存名称", exact: true }).click();
  await expect(page.getByRole("heading", { name: "英语词汇实验室", exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "英语词汇实验室", exact: true })).toBeVisible();
  const doc = await chat(page, request, "请把「英语词汇实验室」这个栏目改名为「英语场景词汇」，只改名字。", "rename-result.json");
  const edits = doc.entries.filter((e: { kind: string; name: string }) => e.kind === "tool" && e.name === "update_series_composition");
  expect(edits).toHaveLength(1); expect(edits[0].status).toBe("done");
  expect(doc.entries.some((e: { name: string }) => ["compose_series", "start_content_run", "research_series_topics"].includes(e.name))).toBe(false);
  await expect(page.getByRole("heading", { name: "英语场景词汇", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "编辑栏目", exact: false }).last().click();
  await expect(page.getByRole("region", { name: "编辑栏目详情", exact: true })).toBeVisible();
  expect(new URL(page.url()).searchParams.get("series")).toBe(fixture.test_series_id);
  await page.screenshot({ path: info.outputPath("rename-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: info.outputPath("rename-mobile.png") });
  await page.reload();
  await expect(page.getByRole("heading", { name: "英语场景词汇", exact: true })).toBeVisible();
  const preserved = await request.get("/test/media-fixture").then(r => r.json());
  expect(preserved.workers_started).toBe(false); expect(preserved.source_images_unchanged).toBe(true);
  expect(preserved.source_run_rows_unchanged).toBe(true);
});

test("rename form is readable on desktop and mobile without model calls", async ({ page }, info) => {
  const fixture = setup();
  await page.goto(`/?creator=${fixture.creator_id}&series=${fixture.test_series_id}`);
  await page.getByRole("button", { name: "栏目设置", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "栏目名称", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "保存名称", exact: true })).toBeDisabled();
  await page.screenshot({ path: info.outputPath("form-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("textbox", { name: "栏目名称", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("form-mobile.png") });
});
