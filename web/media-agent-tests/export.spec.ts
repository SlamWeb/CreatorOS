import { expect, test } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import path from "node:path";

test("account chat with real DeepSeek exports an existing image collection without production", async ({ page, request }, info) => {
  const root = path.resolve(process.env.CREATOROS_MEDIA_AGENT_ROOT!);
  const setup = JSON.parse(readFileSync(path.join(root, "setup.json"), "utf8"));
  const report = path.join(root, "frontend-result.json");
  const writes: { method: string; path: string }[] = [];
  page.on("request", req => { if (!["GET", "HEAD"].includes(req.method())) writes.push({ method: req.method(), path: new URL(req.url()).pathname }); });
  await page.goto(`/?creator=${setup.creator_id}&view=materials&collection=${setup.collection_id}`);
  await page.getByRole("button", { name: "打开账号对话", exact: true }).click();
  const panel = page.locator(".account-chat-panel");
  await expect(panel).toBeVisible();
  const query = `请把当前账号素材集「${setup.name}」里保留的全部图片按现有顺序打包为一个ZIP，给我下载。不要生产、修改Skill、改选片、排序或发布。`;
  await panel.getByRole("textbox", { name: "给 Agent 的消息" }).fill(query);
  const accepted = page.waitForResponse(response => response.request().method() === "POST" && /\/turns$/.test(new URL(response.url()).pathname));
  await panel.getByRole("button", { name: "发送 ↑", exact: true }).click();
  const response = await accepted;
  expect(response.status(), await response.text()).toBe(202);
  const submitted = await response.json();
  writeFileSync(report, JSON.stringify({ query, session_id: submitted.id, phase: "submitted" }, null, 2));
  await expect(panel.locator(".chat-reply-actions")).toHaveCount(1, { timeout: 180_000 });
  const doc = await request.get(`/api/agent/sessions/${submitted.id}`).then(r => r.json());
  const toolRows = doc.entries.filter((entry: { kind: string }) => entry.kind === "tool");
  const replies = doc.entries.filter((entry: { kind: string }) => entry.kind === "assistant");
  const links = replies.flatMap((entry: { links?: unknown[] }) => entry.links ?? []) as { url: string; label: string; kind: string }[];
  const downloadLink = links.find(link => link.kind === "download");
  writeFileSync(report, JSON.stringify({ query, session_id: doc.id, phase: "finished", status: doc.status, error: doc.error,
    tools: toolRows, reply: replies.map((entry: { text: string }) => entry.text).join("\n"), links,
    usage: doc.entries.filter((entry: { kind: string }) => entry.kind === "usage") }, null, 2));
  expect(doc.status).toBe("idle"); expect(doc.error).toBeNull();
  expect(toolRows.some((entry: { name: string; status: string }) => entry.name === "manage_media_collection" && entry.status === "done")).toBe(true);
  expect(toolRows.every((entry: { status: string }) => entry.status === "done")).toBe(true);
  await expect(panel.locator(".chat-answer").last()).not.toContainText("/api/");
  await expect(panel.locator(".chat-answer").last()).not.toContainText("export_id");
  expect(downloadLink).toBeTruthy();
  const href = downloadLink!.url;
  expect(href).toMatch(new RegExp(`^/api/creators/${setup.creator_id}/collections/${setup.collection_id}/exports/[a-f0-9]{64}/download$`));
  const button = panel.getByRole("navigation", { name: "本轮任务入口" }).last().getByRole("link", { name: downloadLink!.label, exact: true });
  await expect(button).toHaveAttribute("download", "");
  const beforeUrl = page.url();
  const downloaded = page.waitForEvent("download");
  await button.click();
  const zip = info.outputPath("agent-images.zip");
  await (await downloaded).saveAs(zip);
  expect(page.url()).toBe(beforeUrl);
  await expect(panel).toBeVisible();
  const zipped = JSON.parse(execFileSync(process.env.CREATOROS_PYTHON ?? "python", ["-c", `
import hashlib,json,sys,zipfile
with zipfile.ZipFile(sys.argv[1]) as z:
    m=json.loads(z.read('manifest.json'))
    print(json.dumps({'count':m['selected_count'],'checksums':[hashlib.sha256(z.read(i['filename'])).hexdigest() for i in m['items']]}))
`, zip], { encoding: "utf8" }));
  expect(zipped.count).toBe(setup.image_count);
  expect(zipped.checksums).toEqual(setup.checksums);
  const oldUserCount = doc.entries.filter((entry: { kind: string }) => entry.kind === "user").length;
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.reload();
  await expect(page.locator(".account-chat-panel")).toBeVisible();
  await expect(page.locator(".account-chat-panel").getByRole("navigation", { name: "本轮任务入口" }).last().getByRole("link", { name: downloadLink!.label, exact: true })).toHaveAttribute("href", href);
  const after = await request.get(`/api/agent/sessions/${doc.id}`).then(r => r.json());
  expect(after.entries.filter((entry: { kind: string }) => entry.kind === "user")).toHaveLength(oldUserCount);
  expect(after.entries).toEqual(doc.entries);
  const preservation = await request.get("/test/media-fixture").then(r => r.json());
  expect(preservation.source_run_rows_unchanged).toBe(true);
  expect(preservation.source_images_unchanged).toBe(true);
  expect(preservation.workers_started).toBe(false);
  expect(writes.every(entry => entry.path === "/api/agent/sessions" || /^\/api\/agent\/sessions\/[a-f0-9-]{36}\/turns$/.test(entry.path))).toBe(true);
  const evidence = JSON.parse(readFileSync(report, "utf8"));
  writeFileSync(report, JSON.stringify({ ...evidence, phase: "verified", zip: zipped, preservation, browser_writes: writes }, null, 2));
});
