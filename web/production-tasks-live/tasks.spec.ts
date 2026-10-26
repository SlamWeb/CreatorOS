import { expect, test } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

test("real frontend DeepSeek delegates two independent Codex tasks into one collection", async ({ page, request }, info) => {
  const root = path.resolve(process.env.CREATOROS_TASKS_LIVE_ROOT!);
  const setup = JSON.parse(readFileSync(path.join(root, "setup.json"), "utf8"));
  await page.goto(`/?creator=${setup.creator_id}&series=${setup.series_id}`);
  await page.getByRole("button", { name: "打开账号对话", exact: true }).click();
  const panel = page.locator(".account-chat-panel");
  const query = `请直接开始生产，这是已授权的隔离验收。已有选题「同一个人，从清晨到黄昏」(ID: ${setup.topic_id})，不要新增或修改选题。把它拆成两个制作任务，第一项「清晨世界」要求只生成一张冷蓝色清晨像素插画，使用栏目默认Skill；第二项「黄昏世界」要求只生成一张暖橘色黄昏像素插画，独立指定「黄昏像素」Skill（ID: ${setup.skills[1].id}，请读取真实digest）。用一次start_content_run的tasks参数提交这两个任务，两个独立Codex线程并行，归同一素材集。不要联网、改Skill、做RSI、发布或自动重试。提交后简短说明进度和素材集入口即可，不反复轮询。`;
  let accepted: { id: string };
  if (process.env.CREATOROS_TASKS_EXISTING_SESSION) {
    // Explicit read-only continuation after a test transport failure; never resubmit production.
    accepted = { id: process.env.CREATOROS_TASKS_EXISTING_SESSION };
  } else {
    await panel.getByRole("textbox", { name: "给 Agent 的消息" }).fill(query);
    const acceptedResponse = page.waitForResponse(response => response.request().method() === "POST" && /\/turns$/.test(new URL(response.url()).pathname));
    await panel.getByRole("button", { name: "发送 ↑", exact: true }).click();
    accepted = await (await acceptedResponse).json();
  }
  await expect.poll(async () => (await request.get(`/api/agent/sessions/${accepted.id}`).then(response => response.json())).status,
    { timeout: 240_000, intervals: [2000, 5000] }).toBe("idle");
  const conversation = await request.get(`/api/agent/sessions/${accepted.id}`).then(response => response.json());
  writeFileSync(path.join(root, "conversation.json"), JSON.stringify({ query, ...conversation }, null, 2));
  expect(conversation.error).toBeNull();
  const submitted = conversation.entries.filter((entry: { kind: string; name: string }) => entry.kind === "tool" && entry.name === "start_content_run");
  expect(submitted).toHaveLength(1);
  const endpoint = `/api/topics/${setup.topic_id}/production-tasks`;
  const initial = await request.get(endpoint).then(response => response.json());
  writeFileSync(path.join(root, "accepted.json"), JSON.stringify(initial, null, 2));
  expect(initial.items).toHaveLength(2);
  expect(new Set(initial.items.map((item: { run_id: string }) => item.run_id)).size).toBe(2);
  await page.screenshot({ path: info.outputPath("real-agent-submitted.png"), fullPage: true });
  await page.getByRole("button", { name: "关闭账号对话", exact: true }).click();
  await page.goto(`/?creator=${setup.creator_id}&series=${setup.series_id}&topic=${setup.topic_id}`);
  const dialog = page.getByRole("dialog", { name: "选题详情" });
  await expect(dialog.locator(".production-task-list > li")).toHaveCount(2);
  await expect.poll(async () => (await request.get(endpoint).then(response => response.json())).items.map((item: { status: string }) => item.status),
    { timeout: 1_200_000, intervals: [5000, 10000] }).toEqual(["awaiting_approval", "awaiting_approval"]);
  const completed = await request.get(endpoint).then(response => response.json());
  const runs = await Promise.all(completed.items.map((item: { run_id: string }) => request.get(`/api/runs/${item.run_id}`).then(response => response.json())));
  const collection = await request.get(`/api/creators/${setup.creator_id}/collections/${completed.collection_id}`).then(response => response.json());
  writeFileSync(path.join(root, "completed.json"), JSON.stringify({ completed, runs, collection }, null, 2));
  expect(collection.items).toHaveLength(2);
  expect(collection.items.map((item: { run_id: string }) => item.run_id)).toEqual(completed.items.map((item: { run_id: string }) => item.run_id));
  await dialog.getByRole("link", { name: "查看素材集", exact: true }).click();
  await expect(page.getByRole("region", { name: "素材文件夹", exact: true })).toBeVisible();
  await expect(page.locator(".media-image-grid img")).toHaveCount(2);
  await page.screenshot({ path: info.outputPath("real-collected.png"), fullPage: true });
  const downloadReady = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载所选 ZIP（2）", exact: true }).click();
  await (await downloadReady).saveAs(path.join(root, "collected-images.zip"));
  await page.reload();
  await expect(page.locator(".media-image-grid img")).toHaveCount(2);
  expect((await request.get(endpoint).then(response => response.json())).items.map((item: { run_id: string }) => item.run_id)).toEqual(initial.items.map((item: { run_id: string }) => item.run_id));
});
