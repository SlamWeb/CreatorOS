import { expect, test } from "@playwright/test";

const chatId = "whitespace-contract-chat";
const session = (extra = {}) => ({ id: chatId, title: "原文验收", version: 0, status: "idle", error: null,
  scope_kind: "overview", creator_id: null, entries: [], updated_at: "2026-11-08T00:00:00Z", has_older: false,
  reasoning_effort: "high", reasoning_options: ["low", "medium", "high", "xhigh", "ultra"], ...extra });

test("submits exact text and keeps later edits made before the response", async ({ page }) => {
  let payload: Record<string, unknown> | undefined;
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route(`**/api/agent/sessions/${chatId}`, route => route.fulfill({ json: session() }));
  await page.route(`**/api/agent/sessions/${chatId}/turns`, async route => {
    payload = route.request().postDataJSON();
    await gate;
    await route.fulfill({ json: session({ version: 2, entries: [{ kind: "user", text: payload?.text, turn_id: payload?.request_id }] }) });
  });
  await page.goto(`/agent?chat=${chatId}`);
  const draft = page.getByRole("textbox", { name: "给 Agent 的消息" });
  const original = "  ---\nname: example\n---\n正文\n\n";
  await draft.fill(original);
  await page.getByRole("button", { name: "发送 ↑" }).click();
  try {
    await expect.poll(() => payload?.text).toBeDefined();
    expect(payload?.text).toBe(original);
    await draft.fill(original + "后来编辑");
  } finally { release(); }
  await expect(page.getByRole("button", { name: "发送 ↑" })).toBeEnabled();
  await expect(draft).toHaveValue(original + "后来编辑");
});

test("unknown result replays the exact original text after refresh", async ({ page }) => {
  let doc = session();
  const submitted: Record<string, unknown>[] = [];
  await page.route(`**/api/agent/sessions/${chatId}`, route => route.fulfill({ json: doc }));
  await page.route(`**/api/agent/sessions/${chatId}/turns`, async route => {
    const payload = route.request().postDataJSON();
    submitted.push(payload);
    if (submitted.length === 1) return route.abort("failed");
    doc = session({ version: 2, entries: [{ kind: "user", text: payload.text, turn_id: payload.request_id }] });
    return route.fulfill({ json: doc });
  });
  await page.goto(`/agent?chat=${chatId}`);
  const original = "  完整原文\n末尾空行\n\n";
  await page.getByRole("textbox", { name: "给 Agent 的消息" }).fill(original);
  await page.getByRole("button", { name: "发送 ↑" }).click();
  await expect(page.getByRole("button", { name: "核对并恢复上次提交" })).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "核对并恢复上次提交" }).click();
  await expect.poll(() => submitted.length).toBe(2);
  expect(submitted[0].text).toBe(original);
  expect(submitted[1]).toEqual(submitted[0]);
  await expect(page.getByRole("textbox", { name: "给 Agent 的消息" })).toHaveValue("");
});
