import { expect, test } from "@playwright/test";

const chatId = "composer-contract-chat";
const session = (extra = {}) => ({ id: chatId, title: "输入栏验收", version: 0, status: "idle", error: null,
  scope_kind: "overview", creator_id: null, entries: [], updated_at: "2026-10-21T01:00:00Z", has_older: false,
  reasoning_effort: "high", reasoning_options: ["low", "medium", "high", "xhigh", "ultra"],
  reasoning_actual_effort: "high", reasoning_effort_map: { low: "low", medium: "high", high: "high", xhigh: "high", ultra: "max" },
  context_usage: null, ...extra });

// Controlled browser contract checks: no model calls or business writes.
test("context ring exposes actual or estimated last-input usage and handles missing data", async ({ page }) => {
  let doc = session({ context_usage: { input_tokens: 15321, context_window: 1000000,
    measurement: "actual", source: "last_model_request", observed_at: "2026-10-21T01:00:00Z", turn_id: "turn-1" },
    entries: [{ kind: "usage", input_tokens: 900000, output_tokens: 200 }] });
  await page.route(`**/api/agent/sessions/${chatId}`, route => route.fulfill({ json: doc }));
  await page.goto(`/agent?chat=${chatId}`);
  const ring = page.getByRole("button", { name: "上下文已用 15,321 / 1M" });
  await ring.hover();
  await expect(page.getByRole("tooltip")).toContainText("上次模型请求 · 实际输入");
  await expect(page.getByRole("tooltip")).not.toContainText("900,000");
  await ring.focus();
  await expect(page.getByRole("tooltip")).toBeVisible();
  await ring.press("Escape");
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  doc = session({ context_usage: { ...doc.context_usage, measurement: "estimate" } });
  await page.reload();
  await ring.click();
  await expect(page.getByRole("tooltip")).toContainText("估算输入");
  doc = session();
  await page.reload();
  await page.getByRole("button", { name: "上下文用量：暂无统计" }).click();
  await expect(page.getByRole("tooltip")).toContainText("首次请求返回用量后显示");
});

test("selected reasoning reaches the turn, locks during submit and persists for that session", async ({ page }) => {
  let doc = session();
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let payload: Record<string, unknown> | undefined;
  await page.route(`**/api/agent/sessions/${chatId}`, route => route.fulfill({ json: doc }));
  await page.route(`**/api/agent/sessions/${chatId}/turns`, async route => {
    payload = route.request().postDataJSON();
    await gate;
    doc = session({ version: 2, reasoning_effort: "high", entries: [{ kind: "user", text: payload?.text, turn_id: payload?.request_id }] });
    await route.fulfill({ json: doc });
  });
  await page.goto(`/agent?chat=${chatId}`);
  const effort = page.getByRole("combobox", { name: "推理程度" });
  await expect(effort).toHaveValue("high");
  await expect(effort.locator("option")).toHaveText(["低", "中", "高", "极高", "ultra"]);
  await expect(effort).toHaveAttribute("title", /中、高、极高当前实际同为 high/);
  await effort.selectOption("high");
  await page.getByRole("textbox", { name: "给 Agent 的消息" }).fill("只读查看账号");
  await page.getByRole("button", { name: "发送 ↑" }).click();
  await expect(effort).toBeDisabled();
  await expect.poll(() => payload?.reasoning_effort).toBe("high");
  release();
  await expect(effort).toBeEnabled();
  await page.reload();
  await expect(effort).toHaveValue("high");
  await page.getByRole("button", { name: "新对话", exact: true }).click();
  await expect(effort).toHaveValue("high");
});

test("unknown response survives refresh and explicitly replays the exact original payload", async ({ page }) => {
  let doc = session();
  const submitted: Record<string, unknown>[] = [];
  await page.route(`**/api/agent/sessions/${chatId}`, route => route.fulfill({ json: doc }));
  await page.route(`**/api/agent/sessions/${chatId}/turns`, async route => {
    const payload = route.request().postDataJSON(); submitted.push(payload);
    if (submitted.length === 1) await route.abort("failed");
    else {
      doc = session({ version: 2, reasoning_effort: "high", entries: [{ kind: "user", text: payload.text, turn_id: payload.request_id }] });
      await route.fulfill({ json: doc });
    }
  });
  await page.goto(`/agent?chat=${chatId}`);
  await page.getByRole("combobox", { name: "推理程度" }).selectOption("high");
  await page.getByRole("textbox", { name: "给 Agent 的消息" }).fill("看看栏目");
  await page.getByRole("button", { name: "发送 ↑" }).click();
  await expect(page.getByRole("button", { name: "核对并恢复上次提交" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("combobox", { name: "推理程度" })).toBeDisabled();
  expect(submitted).toHaveLength(1);
  await page.getByRole("button", { name: "核对并恢复上次提交" }).click();
  await expect(page.getByRole("button", { name: "核对并恢复上次提交" })).toHaveCount(0);
  expect(submitted).toHaveLength(2);
  expect(submitted[1]).toEqual(submitted[0]);
});

test("unknown reasoning setting requires an explicit valid choice", async ({ page }) => {
  await page.route(`**/api/agent/sessions/${chatId}`, route => route.fulfill({ json: session({ reasoning_effort: "future" }) }));
  await page.goto(`/agent?chat=${chatId}`);
  await expect(page.getByRole("combobox", { name: "推理程度" })).toHaveValue("");
  await page.getByRole("textbox", { name: "给 Agent 的消息" }).fill("先不要发送");
  await expect(page.getByRole("button", { name: "发送 ↑" })).toBeDisabled();
  await expect(page.getByRole("alert")).toContainText("当前推理设置不可用");
  await page.getByRole("combobox", { name: "推理程度" }).selectOption("high");
  await expect(page.getByRole("button", { name: "发送 ↑" })).toBeEnabled();
});

test("an accepted turn with a lost response is found by identity without a second POST", async ({ page }) => {
  let doc = session();
  let posts = 0;
  await page.route(`**/api/agent/sessions/${chatId}`, route => route.fulfill({ json: doc }));
  await page.route(`**/api/agent/sessions/${chatId}/turns`, async route => {
    posts++;
    const payload = route.request().postDataJSON();
    doc = session({ version: 2, reasoning_effort: payload.reasoning_effort,
      entries: [{ kind: "user", text: payload.text, turn_id: payload.request_id }] });
    await route.abort("failed");
  });
  await page.goto(`/agent?chat=${chatId}`);
  await page.getByRole("textbox", { name: "给 Agent 的消息" }).fill("已接受的请求不要重发");
  await page.getByRole("button", { name: "发送 ↑" }).click();
  await page.getByRole("button", { name: "核对并恢复上次提交" }).click();
  await expect(page.getByRole("button", { name: "核对并恢复上次提交" })).toHaveCount(0);
  expect(posts).toBe(1);
  await expect(page.getByRole("textbox", { name: "给 Agent 的消息" })).toHaveValue("");
});

test("legacy session and local preference start high without rewriting stored history", async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem("creatoros.agent.reasoning.v1:composer-contract-chat", "none"));
  let posts = 0;
  await page.route(`**/api/agent/sessions/${chatId}`, route => route.fulfill({ json: session({ reasoning_effort: "none" }) }));
  await page.route(`**/api/agent/sessions/${chatId}/turns`, route => { posts++; return route.abort(); });
  await page.goto(`/agent?chat=${chatId}`);
  await expect(page.getByRole("combobox", { name: "推理程度" })).toHaveValue("high");
  expect(await page.evaluate(() => sessionStorage.getItem("creatoros.agent.reasoning.v1:composer-contract-chat"))).toBe("none");
  expect(posts).toBe(0);
});

for (const legacy of ["none", "max"]) test(`legacy pending ${legacy} is replayed unchanged and only the next turn defaults high`, async ({ page }) => {
  const pending = { sessionId: chatId, text: "恢复原请求", request_id: `legacy-${legacy}`, expected_version: 0,
    attachment_ids: [], reasoning_effort: legacy };
  await page.addInitScript(value => {
    sessionStorage.setItem(`creatoros.agent.pending-turn.v1:${value.sessionId}`, JSON.stringify(value));
  }, pending);
  let doc = session({ reasoning_effort: legacy });
  let payload: Record<string, unknown> | undefined;
  await page.route(`**/api/agent/sessions/${chatId}`, route => route.fulfill({ json: doc }));
  await page.route(`**/api/agent/sessions/${chatId}/turns`, route => {
    payload = route.request().postDataJSON();
    doc = session({ version: 2, reasoning_effort: legacy,
      entries: [{ kind: "user", text: payload?.text, turn_id: payload?.request_id }] });
    return route.fulfill({ json: doc });
  });
  await page.goto(`/agent?chat=${chatId}`);
  const effort = page.getByRole("combobox", { name: "推理程度" });
  await expect(effort).toBeDisabled();
  await expect(effort).toHaveValue(legacy);
  await expect(effort.locator("option:checked")).toHaveText(`旧请求：${legacy === "none" ? "关闭" : "最高"}`);
  await page.getByRole("button", { name: "核对并恢复上次提交" }).click();
  await expect(effort).toBeEnabled();
  expect(payload).toEqual({ text: pending.text, request_id: pending.request_id, expected_version: pending.expected_version,
    attachment_ids: pending.attachment_ids, reasoning_effort: legacy });
  await expect(effort).toHaveValue("high");
});
