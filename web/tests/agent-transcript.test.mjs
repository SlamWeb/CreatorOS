import assert from "node:assert/strict";
import test from "node:test";
import { applySessionPatch, newestChatSession, transcriptItems } from "../src/components/agentTranscript.ts";
import { chatLinkKind, chatLinkLabel, uniqueChatLinks } from "../src/components/chatLinks.ts";

const session = () => ({ id: "session-a", version: 1, title: "测试", status: "running", error: null,
  scope_kind: "creator", creator_id: "creator-a", stream_revision: 4,
  updated_at: "2026-10-22T08:00:00Z", has_older: false,
  entries: [{ id: "user-1", kind: "user", text: "帮我检查", turn_id: "turn-1" },
    { id: "reply-1", kind: "assistant", text: "正在", turn_id: "turn-1" }] });

test("stream appends text and upserts by stable ID without mutating history", () => {
  const old = session();
  const result = applySessionPatch(old, { id: old.id, base_revision: 4, stream_revision: 5,
    entries_upsert: [{ id: "reply-1", complete: false }], entries_text_append: [{ id: "reply-1", text: "检查。" }] });
  assert.equal(result.status, "applied");
  assert.equal(result.session.entries[1].text, "正在检查。");
  assert.equal(result.session.entries[0], old.entries[0]);
  assert.equal(old.entries[1].text, "正在");
});

test("replayed and older stream patches never duplicate text", () => {
  const old = session();
  for (const revision of [4, 3]) {
    const result = applySessionPatch(old, { id: old.id, base_revision: revision - 1, stream_revision: revision,
      entries_upsert: [], entries_text_append: [{ id: "reply-1", text: "重复" }] });
    assert.equal(result.status, "ignored");
    assert.equal(result.session, old);
  }
});

test("a gap, wrong session, missing IDs and unknown append require a fresh snapshot", () => {
  const old = session();
  const patch = { id: old.id, base_revision: 4, stream_revision: 5, entries_upsert: [] };
  for (const candidate of [{ ...patch, base_revision: 3 }, { ...patch, id: "other" },
    { ...patch, entries_text_append: [{ id: "unknown", text: "不能猜" }] },
    { ...patch, entries_upsert: [{ id: "new-tool" }] },
    { ...patch, creator_id: "creator-b" }, { ...patch, scope_kind: "overview" }]) {
    assert.equal(applySessionPatch(old, candidate).status, "resync");
  }
  assert.equal(applySessionPatch({ ...old, stream_revision: undefined }, patch).status, "resync");
});

test("explicit order supports new tools and truncation but rejects duplicates or missing targets", () => {
  const old = session();
  const patch = { id: old.id, base_revision: 4, stream_revision: 5,
    entries_upsert: [{ id: "tool-1", kind: "tool", status: "running", name: "manage_topics" }],
    entry_order: ["reply-1", "tool-1"], has_older: true };
  const result = applySessionPatch(old, patch);
  assert.equal(result.status, "applied");
  assert.deepEqual(result.session.entries.map(entry => entry.id), ["reply-1", "tool-1"]);
  assert.equal(result.session.has_older, true);
  for (const order of [["reply-1", "reply-1"], ["reply-1", "missing"], ["reply-1"]]) {
    assert.equal(applySessionPatch(old, { ...patch, entry_order: order }).status, "resync");
  }
});

test("new entries are upserted before their first text append", () => {
  const old = session();
  const result = applySessionPatch(old, { id: old.id, base_revision: 4, stream_revision: 5,
    entries_upsert: [{ id: "reply-2", kind: "assistant" }],
    entries_text_append: [{ id: "reply-2", text: "下一步" }], entry_order: ["user-1", "reply-1", "reply-2"] });
  assert.equal(result.status, "applied");
  assert.equal(result.session.entries[2].text, "下一步");
});

test("newer stream state cannot be overwritten by an older polling response", () => {
  const current = session();
  assert.equal(newestChatSession(current, { ...current, stream_revision: 3, updated_at: "2099" }), current);
  assert.equal(newestChatSession(current, { ...current, stream_revision: 5 }).stream_revision, 5);
});

test("one usage summary per explicit turn deduplicates request IDs", () => {
  const rows = transcriptItems([
    { id: "user", kind: "user", text: "生产", turn_id: "turn-1" },
    { id: "u1", kind: "usage", turn_id: "turn-1", model_request_id: "r1", input_tokens: 100, output_tokens: 10 },
    { id: "tool", kind: "tool", turn_id: "turn-1" },
    { id: "u2", kind: "usage", turn_id: "turn-1", model_request_id: "r2", input_tokens: 120, output_tokens: 20 },
    { id: "u2-again", kind: "usage", turn_id: "turn-1", model_request_id: "r2", input_tokens: 120, output_tokens: 20 },
    { id: "reply", kind: "assistant", turn_id: "turn-1", text: "已完成" },
  ]);
  assert.equal(rows.filter(row => row.usage).length, 1);
  assert.equal(rows.at(-1).key, "usage:turn-1");
  assert.deepEqual(rows.at(-1).usage, { turnId: "turn-1", input: 220, output: 30, requests: 2, partial: false });
});

test("legacy usage is not assigned to a neighboring turn or turned into zero", () => {
  const rows = transcriptItems([{ kind: "user", turn_id: "new", text: "新请求" },
    { id: "legacy", kind: "usage", input_tokens: 50 },
    { id: "unknown", kind: "usage", turn_id: "new", model_request_id: "request" },
    { kind: "assistant", turn_id: "new", text: "回答" }]);
  const usages = rows.filter(row => row.usage).map(row => row.usage);
  assert.deepEqual(usages, [
    { turnId: undefined, input: 50, output: null, requests: 1, partial: true },
    { turnId: "new", input: null, output: null, requests: 1, partial: true },
  ]);
});

test("duplicate task receipts display at the last occurrence in the same turn only", () => {
  const link = { url: "/runs/123", label: "旧名字", source_tool: "start_content_run" };
  const rows = transcriptItems([{ id: "t", kind: "tool", turn_id: "one", links: [link] },
    { id: "a", kind: "assistant", turn_id: "one", links: [link], text: "好了" },
    { id: "b", kind: "assistant", turn_id: "two", links: [link], text: "再查看" }]);
  assert.equal(rows[0].hiddenLinkUrls, JSON.stringify([link.url]));
  assert.equal(rows[1].hiddenLinkUrls, "[]");
  assert.equal(rows[2].hiddenLinkUrls, "[]");
});

test("link titles and kinds come from host fields, not Chinese label matching", () => {
  const link = { url: "/runs/6ab3a071-1977-45c7-a14e-a26dc54af892", label: "查看内容", source_tool: "start_content_run",
    kind: "production", title: "朱元璋：村口到宫殿", version: 2 };
  assert.equal(chatLinkLabel(link), "朱元璋：村口到宫殿 · 第 2 版");
  assert.equal(chatLinkKind({ ...link, label: "任意语言" }), "production");
  assert.equal(chatLinkKind({ url: link.url, label: "旧链接", source_tool: "old" }), "production");
  assert.equal(chatLinkKind({ url: "/skills?iteration=abc", label: "旧链接", source_tool: "old" }), "iteration");
  assert.deepEqual(uniqueChatLinks([{ ...link, title: undefined }, link, link]), [link]);
});

test("fresh Runs with the same topic and version keep their host-issued identity", () => {
  const first = { url: "/runs/6ab3a071-1977-45c7-a14e-a26dc54af892", label: "后端入口", source_tool: "start_content_run",
    kind: "production", title: "朱元璋：村口到宫殿", version: 1, run_id: "6ab3a071-1977-45c7-a14e-a26dc54af892" };
  const second = { ...first, url: "/runs/7ab3a071-1977-45c7-a14e-a26dc54af892", run_id: "7ab3a071-1977-45c7-a14e-a26dc54af892" };
  assert.equal(chatLinkLabel(first), "朱元璋：村口到宫殿 · 第 1 版 · 6ab3a0");
  assert.notEqual(chatLinkLabel(first), chatLinkLabel(second));
  assert.equal(chatLinkLabel({ ...first, run_id: undefined }), "朱元璋：村口到宫殿 · 第 1 版");
  assert.equal(chatLinkLabel({ ...first, run_id: "not-a-run" }), "朱元璋：村口到宫殿 · 第 1 版");
});
