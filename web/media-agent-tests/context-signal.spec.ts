import { expect, test } from "@playwright/test";
import { writeFileSync } from "node:fs";
import path from "node:path";

test("real DS can author, read, save and edit Skill with concise receipts", async ({ page, request }) => {
  const root = path.resolve(process.env.CREATOROS_MEDIA_AGENT_ROOT!);
  const reports: unknown[] = [];
  const body = `---\nname: context-signal-probe\ndescription: 隔离上下文去重验证\ncreatoros-output: social-content-pack.image-carousel\n---\n\n# 场景词汇卡\n\n每篇只讲一个生活场景，单词和中文释义逐项对应。\n固定三比四竖版；标题简洁，不添加水印；参考图中的人物形象保持一致。\n\n## few-shot\n在车站等车：platform 站台，ticket 车票，suitcase 行李箱。\n在公园散步：bench 长椅，path 小路，fountain 喷泉。\n`;
  await page.goto("/?creator=author-live-a");
  await page.getByRole("button", { name: "打开账号对话", exact: true }).click();
  const panel = page.locator(".account-chat-panel");
  let sessionId = "";
  for (const query of [
    `请把下面完整说明直接创建为Skill草稿，名称context-signal-probe，角色完整制作Skill。不要调用Codex重新提炼。创建后读取草稿核对原文，再回复我；先不要入库、建栏目或生产。\n\n${body}`,
    "正文我确认了，请把刚才那份草稿保存入库，并用这份完整Skill创建栏目「上下文验证栏目」，归属当前账号。不要生产。",
    "请修改刚保存的context-signal-probe：先读取根目录SKILL.md，然后在末尾增加一行「CONTEXT_SIGNAL_EDIT_20261026」。其他内容原样保留，保存成候选即可，不切栏目、不生产。",
  ]) {
    await panel.getByRole("textbox", { name: "给 Agent 的消息" }).fill(query);
    const accepted = page.waitForResponse(r => r.request().method() === "POST" && /\/turns$/.test(new URL(r.url()).pathname));
    await panel.getByRole("button", { name: "发送 ↑", exact: true }).click();
    const response = await accepted;
    expect(response.status(), await response.text()).toBe(202);
    const submitted = await response.json(); sessionId = submitted.id;
    await expect.poll(async () => (await request.get(`/api/agent/sessions/${sessionId}`).then(r => r.json())).status,
      { timeout: 180_000 }).toBe("idle");
    const doc = await request.get(`/api/agent/sessions/${sessionId}`).then(r => r.json());
    reports.push({ query, ...doc });
    writeFileSync(path.join(root, "frontend-context-result.json"), JSON.stringify(reports, null, 2));
    expect(doc.error).toBeNull();
    expect(doc.entries.filter((e: { kind: string; status?: string }) => e.kind === "tool").every((e: { status: string }) => e.status === "done")).toBe(true);
  }
  await expect(panel.locator(".chat-reply-actions")).toHaveCount(3);
  const evidence = await request.get("/api/author-probe/evidence").then(r => r.json());
  writeFileSync(path.join(root, "frontend-context-evidence.json"), JSON.stringify(evidence, null, 2));
  expect(evidence.forbidden_calls).toEqual([]);
  expect(evidence.runs_count).toBe(0);
  expect(evidence.series).toHaveLength(1);
  expect(evidence.series[0].name).toBe("上下文验证栏目");
  await page.reload();
  await expect(page.locator(".account-chat-panel").locator(".chat-reply-actions")).toHaveCount(3);
});
