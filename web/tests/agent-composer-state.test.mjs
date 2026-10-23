import assert from "node:assert/strict";
import test from "node:test";
import { contextFocusDraftKey, contextUsageView, initialReasoningEffort, parseContextFocusDraft,
  parsePendingAgentTurn, isReasoningEffort, REASONING_EFFORTS, sameContextTarget } from "../src/components/agentComposerState.ts";

test("context displays the last request input instead of a running sum", () => {
  const value = contextUsageView({ input_tokens: 15321, context_window: 1000000,
    measurement: "actual", source: "last_model_request", observed_at: "2026-10-21T01:00:00Z", turn_id: "turn-1" });
  assert.equal(value.label, "上下文已用 15,321 / 1M");
  assert.equal(value.source, "上次模型请求 · 实际输入");
  assert.equal(value.ratio, .015321);
});
test("estimates stay labelled and absent or malformed usage never becomes zero", () => {
  assert.equal(contextUsageView(null), null);
  assert.equal(contextUsageView({ input_tokens: -1, context_window: 1e6 }), null);
  assert.equal(contextUsageView({ input_tokens: NaN, context_window: 1e6 }), null);
  const usage = { input_tokens: 1200000, context_window: 1000000, measurement: "estimate", source: "last_model_request" };
  assert.equal(contextUsageView(usage).ratio, 1);
  assert.match(contextUsageView(usage).source, /估算/);
  assert.equal(contextUsageView({ ...usage, measurement: "unknown" }), null);
});
test("pending recovery preserves the original reasoning, identity and version", () => {
  const pending = { sessionId: "chat-a", text: "看一下栏目", request_id: "request-a", expected_version: 3,
    attachment_ids: ["image-a"], reasoning_effort: "high" };
  assert.deepEqual(parsePendingAgentTurn(JSON.stringify(pending), "chat-a"), pending);
  assert.equal(parsePendingAgentTurn(JSON.stringify(pending), "chat-b"), null);
  assert.equal(parsePendingAgentTurn(JSON.stringify({ ...pending, reasoning_effort: "unexpected" }), "chat-a"), null);
  assert.equal(parsePendingAgentTurn("invalid json", "chat-a"), null);
  assert.equal(isReasoningEffort("high"), true);
  assert.equal(isReasoningEffort("xhigh"), true);
  assert.equal(isReasoningEffort("none"), false);
});
test("new choices default high while legacy pending requests retain their exact values", () => {
  assert.deepEqual(REASONING_EFFORTS, ["low", "medium", "high", "xhigh", "ultra"]);
  for (const legacy of ["none", "max"]) {
    assert.equal(initialReasoningEffort(legacy), "high");
    const pending = { sessionId: "chat-a", text: "旧请求", request_id: "request-a", expected_version: 2,
      attachment_ids: [], reasoning_effort: legacy };
    assert.deepEqual(parsePendingAgentTurn(JSON.stringify(pending), "chat-a"), pending);
  }
  assert.equal(initialReasoningEffort(), "high");
  assert.equal(initialReasoningEffort("medium"), "medium");
  assert.equal(initialReasoningEffort("future"), "future");
});

test("RSI authorization is frozen as a boolean and legacy pending requests remain unchanged", () => {
  const pending = { sessionId: "chat-a", text: "迭代 Skill", request_id: "iteration-a", expected_version: 3,
    attachment_ids: [], reasoning_effort: "high", iteration_mode: true };
  assert.deepEqual(parsePendingAgentTurn(JSON.stringify(pending), "chat-a"), pending);
  assert.equal(parsePendingAgentTurn(JSON.stringify({ ...pending, iteration_mode: "true" }), "chat-a"), null);
  assert.equal(parsePendingAgentTurn(JSON.stringify({ ...pending, iteration_mode: 1 }), "chat-a"), null);
});

test("pending recovery freezes exact topic, Run and revision identities", () => {
  const pending = { sessionId: "chat-a", text: "讨论这条选题", request_id: "request-a", expected_version: 3,
    attachment_ids: [], reasoning_effort: "high", context_target: {
      series_id: "series-a", topic_id: "topic-a", run_id: "run-a", revision_id: "revision-a" } };
  const restored = parsePendingAgentTurn(JSON.stringify(pending), "chat-a");
  assert.deepEqual(restored, pending);
  for (const context_target of [null, {}, { series_id: "series-a", topic_id: "topic-a", creator_id: "other" },
    { series_id: "series-a", topic_id: "../topic-a" }, { series_id: "series-a", topic_id: "topic-a", run_id: null },
    { series_id: "series-a", topic_id: "topic-a", revision_id: "revision-a" }]) {
    assert.equal(parsePendingAgentTurn(JSON.stringify({ ...pending, context_target }), "chat-a"), null);
  }
});

test("focus drafts restore names separately from identity and remain scoped to one account", () => {
  const focus = { target: { series_id: "series-a", topic_id: "topic-a" }, title: "地球 online：柯基版" };
  assert.deepEqual(parseContextFocusDraft(JSON.stringify(focus)), focus);
  assert.notEqual(contextFocusDraftKey("creator:a"), contextFocusDraftKey("creator:b"));
  assert.notEqual(contextFocusDraftKey("creator:a"), contextFocusDraftKey("overview"));
  assert.equal(parseContextFocusDraft(JSON.stringify({ ...focus, title: " " })), null);
  assert.equal(parseContextFocusDraft(JSON.stringify({ ...focus, target: { ...focus.target, path: "D:/x" } })), null);
  assert.equal(parseContextFocusDraft("null"), null);
  assert.equal(sameContextTarget(focus.target, { topic_id: "topic-a", series_id: "series-a" }), true);
  assert.equal(sameContextTarget(focus.target, { ...focus.target, topic_id: "topic-b" }), false);
  assert.equal(sameContextTarget(focus.target, { ...focus.target, run_id: "run-a" }), false);
});
