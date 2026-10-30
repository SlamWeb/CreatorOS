import assert from "node:assert/strict";
import test from "node:test";
import { compactTranscript, compactReplyText, toolBatchStatus } from "../src/components/compactTranscript.ts";

const item = (id, kind = "tool", extra = {}) => ({ key: id, entry: { id, kind, turn_id: "turn-a", status: "done", ...extra } });

test("adjacent tools in one turn collapse without losing IDs, order or failures", () => {
  const entries = [item("u", "user"), item("a"), item("b", "tool", { status: "failed" }), item("c"), item("reply", "assistant")];
  const before = JSON.stringify(entries);
  const groups = compactTranscript(entries);
  assert.deepEqual(groups.map(group => group.items.length), [1, 3, 1]);
  assert.equal(groups[1].key, "a");
  assert.deepEqual(groups[1].items.map(value => value.entry.id), ["a", "b", "c"]);
  assert.equal(toolBatchStatus(groups[1].items).label, "3 次调用 · 1 次需检查");
  assert.equal(JSON.stringify(entries), before);
});

test("turn boundaries, legacy entries and activity widgets do not get swallowed", () => {
  const entries = [item("a"), item("b", "tool", { turn_id: "other" }), item("c", "tool", { turn_id: undefined }),
    item("d", "tool", { research: {} }), item("e"), item("f", "assistant"), item("g")];
  assert.deepEqual(compactTranscript(entries).map(group => group.items.length), [1, 1, 1, 1, 1, 1, 1]);
});

test("running and unknown outcomes are explicit, never called production success", () => {
  const state = toolBatchStatus([item("a"), item("b", "tool", { status: "running" }), item("c", "tool", { status: undefined })]);
  assert.equal(state.label, "3 次调用 · 1 次调用中 · 1 次需检查");
  assert.equal(state.attention, 1);
});

test("only standalone confirmed link lists are removed, prose and citations survive", () => {
  const confirmed = url => url === "/runs/a" || url === "/runs/b";
  assert.equal(compactReplyText('已提交。\n\n[甲](/runs/a "甲")[乙](/runs/b)', confirmed), "已提交。");
  for (const text of ["请检查[甲](/runs/a)，它失败了。", "[资料](https://example.com)", "[未知](/runs/unknown)", "- [甲](/runs/a)"]) {
    assert.equal(compactReplyText(text, confirmed), text);
  }
});
