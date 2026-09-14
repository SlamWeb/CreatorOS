import { expect, test } from "@playwright/test";

test("agent history, markdown and composer remain usable", async ({ page }, info) => {
  const entries = [
    { kind: "user", text: "查看我的账号和栏目，先不要生产。" },
    { kind: "tool", name: "list_creators", status: "done" },
    { kind: "assistant", text: "找到一个运营账号：\n\n| 名称 | 平台 | 账号 ID | 状态 |\n| --- | --- | --- | --- |\n| AI Agent 面试知识库 | 小红书 | `creator-376d01efd48e44dcaccb` | 已启用 |\n\n该账号的栏目是 **每天图解一个 AI 概念**，目前还没有入队选题。\n\n- 可以先调研候选\n- 选好后生成预览，确认才入队\n\n```python\nprint('只查询，不生产')\n```\n\n<script>alert('unsafe')</script>" },
    { kind: "usage", input_tokens: 2340, output_tokens: 189 },
  ];
  const docs = [
    { id: "one", title: "查看账号与栏目", version: 1, status: "idle", error: null, entries, updated_at: "2026-09-13T08:00:00Z", has_older: false },
    { id: "two", title: "知识栏目选题调研与待生产队列确认", version: 1, status: "idle", error: null, entries: [{ kind: "assistant", text: "另一段独立对话。" }], updated_at: "2026-09-12T08:00:00Z", has_older: false },
  ];
  let posts = 0;
  await page.route("**/api/agent/sessions**", async route => {
    const url = new URL(route.request().url());
    if (route.request().method() === "POST") {
      posts++;
      return route.fulfill({ status: 503, json: { detail: "测试：提交暂不可用" } });
    }
    if (url.pathname.endsWith("/events")) return route.abort();
    const doc = docs.find(d => url.pathname.endsWith("/" + d.id));
    return route.fulfill({ json: doc ?? { items: docs } });
  });
  await page.goto("/agent?chat=one");
  await expect(page.getByText("找到一个运营账号：")).toBeVisible();
  if (process.env.AGENT_BASELINE) {
    await page.screenshot({ path: "../tmp/agent-ui/before-desktop.png", fullPage: true });
    return;
  }
  await expect(page.getByRole("table")).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "平台" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "小红书" })).toBeVisible();
  await expect(page.locator(".chat-answer script")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /查看账号与栏目/ })).toHaveAttribute("aria-current", "page");
  await page.screenshot({ path: info.outputPath("agent-desktop.png"), fullPage: true });
  await page.getByRole("link", { name: "添加 / 调整选题 ↗" }).click();
  await expect(page.getByRole("dialog", { name: "运营指令" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(/chat=one/);
  await expect(page.getByRole("table")).toBeVisible();
  await page.getByRole("button", { name: /知识栏目选题调研/ }).click();
  await expect(page.getByText("另一段独立对话。")).toBeVisible();
  await page.reload();
  await expect(page.getByText("另一段独立对话。")).toBeVisible();
  expect(posts).toBe(0);
  await page.getByRole("button", { name: "新对话", exact: true }).click();
  await expect(page.getByRole("heading", { name: "从你已有的账号开始" })).toBeVisible();
  expect(posts).toBe(0);
  await page.getByRole("button", { name: /查看账号与栏目/ }).click();
  const input = page.getByRole("textbox", { name: "给 Agent 的消息" });
  await input.fill("测试消息");
  await input.dispatchEvent("keydown", { key: "Enter", isComposing: true });
  expect(posts).toBe(0);
  await input.press("Shift+Enter");
  await expect(input).toHaveValue("测试消息\n");
  await input.press("Enter");
  await expect(page.getByRole("alert")).toContainText("没有自动重试");
  await expect(input).toHaveValue("测试消息\n");
  expect(posts).toBe(1);
  await page.screenshot({ path: info.outputPath("agent-error.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await expect(page.getByRole("table")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const scroller = page.getByRole("region", { name: "表格，可横向滚动" });
  await scroller.focus();
  await page.keyboard.press("End");
  await scroller.evaluate(el => { el.scrollLeft = el.scrollWidth; });
  expect(await scroller.evaluate(el => el.scrollLeft)).toBeGreaterThan(0);
  await scroller.evaluate(el => { el.scrollLeft = 0; });
  await page.screenshot({ path: info.outputPath("agent-mobile.png"), fullPage: true });
  await page.getByRole("button", { name: "历史对话", exact: true }).click();
  await page.getByRole("button", { name: /知识栏目选题调研/ }).click();
  await expect(page.getByText("另一段独立对话。")).toBeVisible();
  await expect(page.getByRole("button", { name: "历史对话", exact: true })).toHaveAttribute("aria-expanded", "false");
  expect(posts).toBe(1);
});
