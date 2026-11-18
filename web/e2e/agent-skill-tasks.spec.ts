import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";

const chatId = "904fb57b-0a5f-4c0c-ab72-be48a77052f9";
const creatorId = "review-lab";
const creatorUrl = `/agent?creator=${creatorId}&chat=${chatId}`;
const endpoint = `/api/agent/sessions/${chatId}`;
const session = (extra = {}) => ({
  id: chatId, title: "Skill 任务验收", version: 0, status: "idle", error: null,
  scope_kind: "creator", creator_id: creatorId, entries: [], updated_at: "2026-11-19T01:00:00Z",
  has_older: false, reasoning_effort: "high", ...extra,
});

test("script authorization survives an unknown submit as part of the original turn only", async ({ page }, testInfo) => {
  const submitted: Record<string, unknown>[] = [];
  let doc = session();
  await page.route(`**/api/creators/${creatorId}`, route => route.fulfill({
    json: { id: creatorId, is_active: true, display_name: "Skill 测试账号" },
  }));
  await page.route(`**${endpoint}`, route => route.fulfill({ json: doc }));
  await page.route(`**${endpoint}/plans`, route => route.fulfill({ json: { items: [] } }));
  await page.route(`**${endpoint}/turns`, async route => {
    const payload = route.request().postDataJSON();
    submitted.push(payload);
    if (submitted.length === 1) {
      await route.abort("failed");
      return;
    }
    doc = session({ version: doc.version + 2, entries: [
      ...doc.entries, { kind: "user", text: payload.text, turn_id: payload.request_id },
    ] });
    await route.fulfill({ status: 202, json: doc });
  });
  await page.goto(creatorUrl);

  const authorization = page.getByRole("button", { name: "允许本轮运行脚本" });
  const iteration = page.getByRole("button", { name: "RSI 迭代模式" });
  const draft = page.getByRole("textbox", { name: "给 Agent 的消息" });
  const send = page.getByRole("button", { name: "发送 ↑" });
  await expect(authorization).toHaveAttribute("aria-pressed", "false");
  await iteration.click();
  await expect(iteration).toHaveAttribute("aria-pressed", "true");
  await authorization.focus();
  await authorization.press("Space");
  await expect(authorization).toHaveAttribute("aria-pressed", "true");
  await expect(iteration).toHaveAttribute("aria-pressed", "false");
  const pressedColor = await authorization.evaluate(button => getComputedStyle(button).color);
  await page.screenshot({ path: testInfo.outputPath("authorization-desktop.png") });
  await draft.fill("把脚本结果导出给我");
  await send.click();
  await expect(page.getByText("上次提交待核对")).toBeVisible();
  expect(submitted[0].skill_workspace).toBe(true);

  await page.reload();
  await expect(authorization).toBeDisabled();
  await expect(authorization).toHaveAttribute("aria-pressed", "true");
  expect(submitted).toHaveLength(1);
  await page.getByRole("button", { name: "核对并恢复上次提交" }).click();
  await expect.poll(() => submitted.length).toBe(2);
  expect(submitted[1]).toEqual(submitted[0]);
  await expect(authorization).toBeEnabled();
  await expect(authorization).toHaveAttribute("aria-pressed", "false");

  await draft.fill("这次只写文案");
  await send.click();
  await expect.poll(() => submitted.length).toBe(3);
  expect(submitted[2].skill_workspace).toBe(false);

  await authorization.click();
  await page.getByRole("button", { name: "新对话", exact: true }).click();
  await expect(page).not.toHaveURL(/chat=/);
  await expect(authorization).toHaveAttribute("aria-pressed", "false");
  await page.goBack();
  await expect(page).toHaveURL(creatorUrl);
  await expect(authorization).toHaveAttribute("aria-pressed", "false");
  await page.setViewportSize({ width: 390, height: 844 });
  await authorization.click();
  await expect(authorization).toHaveAttribute("aria-pressed", "true");
  await expect(authorization).toHaveCSS("color", pressedColor);
  await expect(authorization).toBeInViewport();
  await expect(send).toBeInViewport();
  await page.screenshot({ path: testInfo.outputPath("authorization-mobile.png") });
  await page.goto("/agent");
  await expect(authorization).toHaveCount(0);
});

test("only a verified same-session artifact receipt renders a download", async ({ page, request }) => {
  const seeded = await request.post("/test/skill-task-download", { data: {} });
  expect(seeded.ok(), await seeded.text()).toBe(true);
  const delivered = await seeded.json();
  const { endpoint: prefix, url: download, session_id: sid, turn_id: taskId, creator_id: creator } = delivered;
  const currentSession = `/api/agent/sessions/${sid}`;
  const links = [
    { url: download, label: "下载结果.txt" },
    { url: `${prefix}?path=out%2F..%2Fprivate.txt`, label: "越权附件" },
    { url: `${prefix}?path=out%2Ftest.txt&path=out%2Fsecond.txt`, label: "重复参数" },
    { url: download.replace(sid, taskId), label: "其他会话" },
  ].map(link => ({ ...link, kind: "download", source_tool: "manage_skill_task" }));
  await page.route(`**${currentSession}`, route => route.fulfill({ json: session({ id: sid, creator_id: creator, entries: [
    { kind: "tool", name: "manage_skill_task", status: "done", turn_id: taskId, links },
    { kind: "assistant", text: `处理完成。[跨源冒充](https://example.test${download})`, complete: true,
      turn_id: taskId, links },
  ] }) }));
  await page.goto(`/agent?creator=${creator}&chat=${sid}`);
  await page.getByRole("button", { name: /查看结果/ }).click();
  await page.getByRole("button", { name: /执行 Skill 任务/ }).click();
  const link = page.getByRole("link", { name: "下载结果.txt" });
  await expect(link).toHaveCount(1);
  await expect(link).toHaveAttribute("href", download);
  await expect(page.getByRole("link", { name: /越权附件|重复参数|其他会话|跨源冒充/ })).toHaveCount(0);
  const started = page.waitForEvent("download");
  await link.click();
  const file = await started;
  expect(file.suggestedFilename()).toBe("结果.txt");
  expect(await readFile(await file.path(), "utf8")).toBe("受控验证");
});
