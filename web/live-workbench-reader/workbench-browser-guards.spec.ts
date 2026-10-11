import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { expect, request as playwrightRequest, test } from "@playwright/test";
import { preservePrimaryFailure } from "../live-workbench/preserve-primary-failure";
import { readTask } from "../live-workbench/read-progress";
import { assertDiscussionRecords, assertDiscussionRefresh, discussionTranscript, imageInfo, readDiscussionDelegation, readCompletedDiscussionTranscript,
  sentDiscussionQueries } from "../live-workbench/discussion-rework-journey";

test("readTask recovers a real HTTP reset with GET only", async () => {
  const methods: string[] = [];
  const server = createServer((incoming, outgoing) => {
    methods.push(incoming.method ?? "");
    if (methods.length === 1) {
      incoming.socket.destroy();
      return;
    }
    outgoing.writeHead(200, { "content-type": "application/json" });
    outgoing.end(JSON.stringify({ id: "run-local", status: "awaiting_approval" }));
  });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address() as AddressInfo;
  const context = await playwrightRequest.newContext({ baseURL: `http://127.0.0.1:${address.port}` });
  try {
    const browser: Record<string, unknown> = {};
    const task = await readTask(context, "/api/runs/run-local", browser);
    expect(task.status).toBe("awaiting_approval");
    expect(methods).toEqual(["GET", "GET"]);
    const retries = browser.observation_retries as Array<{ recovery: string; http_status: number | null }>;
    expect(retries.map(row => row.recovery)).toEqual(["retrying", "recovered"]);
    expect(retries.map(row => row.http_status)).toEqual([null, 200]);
    expect(methods.some(method => method !== "GET")).toBe(false);
  } finally {
    await context.dispose();
    await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  }
});

test("finalization error is recorded without replacing the primary journey error", async () => {
  const primary = new Error("primary journey failed");
  const secondary = new Error("Eval reader failed");
  let recorded: unknown;
  const result = await preservePrimaryFailure(primary, async () => { throw secondary; }, error => { recorded = error; });
  expect(result).toBe(primary);
  expect(recorded).toBe(secondary);
});

test("failed discussion reads only sent queries and still rejects altered or repeated inputs", () => {
  const frozen = ["先看这一版", "继续上一轮"];
  const post = (text: string) => ({ method: "POST", path: "/api/agent/sessions/session-1/turns", body: JSON.stringify({ text }) });
  const first = post(frozen[0]);
  expect(sentDiscussionQueries([], frozen)).toEqual([]);
  expect(sentDiscussionQueries([first], frozen)).toEqual([frozen[0]]);
  expect(sentDiscussionQueries([first, post(frozen[1])], frozen)).toEqual(frozen);
  for (const rows of [[post("改题")], [first, first], [first, post(frozen[1]), post(frozen[1])]]) {
    expect(() => sentDiscussionQueries(rows, frozen)).toThrow(/准确前缀/);
  }
  expect(() => sentDiscussionQueries([{ ...first, body: "{}" }], frozen)).toThrow(/准确前缀/);
});

const discussionTurns = [{ turn_id: "turn-one", question: "先看这一版" }, { turn_id: "turn-two", question: "继续上一轮" }];
type DiscussionSession = Parameters<typeof discussionTranscript>[0];
function completedDiscussionSession(): DiscussionSession {
  return { id: "session-one", status: "idle", error: null, has_older: false, entries: [
    { kind: "user", text: discussionTurns[0].question, turn_id: discussionTurns[0].turn_id },
    { kind: "assistant", text: "我先核实这一版。", turn_id: "turn-one", complete: false, terminal: false },
    { kind: "assistant", text: "**首轮最终诊断**。", turn_id: "turn-one", complete: true, terminal: true },
    { kind: "user", text: discussionTurns[1].question, turn_id: discussionTurns[1].turn_id },
    { kind: "assistant", text: "", turn_id: "turn-two", complete: false, terminal: false },
    { kind: "assistant", text: "第二轮更短释义。", turn_id: "turn-two", complete: true, terminal: true },
  ] };
}

test("discussion transcript keeps public progress and exactly two completed replies bound to sent turns", () => {
  const transcript = discussionTranscript(completedDiscussionSession(), discussionTurns);
  expect(transcript.public_bodies).toEqual(["我先核实这一版。", "**首轮最终诊断**。", "第二轮更短释义。"]);
  expect(transcript.final_replies).toEqual([
    { turn_id: "turn-one", text: "**首轮最终诊断**。", public_index: 1 },
    { turn_id: "turn-two", text: "第二轮更短释义。", public_index: 2 },
  ]);
  assertDiscussionRefresh(transcript, structuredClone(transcript), ["说明", "诊断", "释义"], ["说明", "诊断", "释义"]);
});

test("discussion transcript rejects a missing or duplicate final reply", () => {
  for (const index of [2, 5]) {
    const missing = completedDiscussionSession();
    missing.entries.splice(index, 1);
    expect(() => discussionTranscript(missing, discussionTurns)).toThrow(/恰有一条/);
  }
  const duplicate = completedDiscussionSession();
  duplicate.entries.push({ ...duplicate.entries[5] });
  expect(() => discussionTranscript(duplicate, discussionTurns)).toThrow(/恰有一条/);
});

test("discussion transcript rejects terminal partials, missing completion flags and empty final text", () => {
  for (const flags of [{ complete: false, terminal: true }, { complete: true, terminal: false },
    { complete: false, terminal: false }, { complete: undefined, terminal: undefined }]) {
    const incomplete = completedDiscussionSession();
    Object.assign(incomplete.entries[5], flags);
    expect(() => discussionTranscript(incomplete, discussionTurns)).toThrow(/恰有一条/);
  }
  for (const text of ["", "   "]) {
    const empty = completedDiscussionSession();
    empty.entries[5].text = text;
    expect(() => discussionTranscript(empty, discussionTurns)).toThrow(/恰有一条/);
  }
});

test("discussion transcript rejects wrong turn identities, altered questions and reversed final order", () => {
  for (const turnId of ["foreign-turn", "turn-one", undefined]) {
    const wrong = completedDiscussionSession();
    wrong.entries[5].turn_id = turnId;
    expect(() => discussionTranscript(wrong, discussionTurns)).toThrow(/身份与顺序/);
  }
  const reversed = completedDiscussionSession();
  [reversed.entries[2], reversed.entries[5]] = [reversed.entries[5], reversed.entries[2]];
  expect(() => discussionTranscript(reversed, discussionTurns)).toThrow(/身份与顺序/);
  const altered = completedDiscussionSession();
  altered.entries[3].text = "不是实际 POST 的问题";
  expect(() => discussionTranscript(altered, discussionTurns)).toThrow();
  expect(() => discussionTranscript(completedDiscussionSession(), [discussionTurns[1], discussionTurns[0]])).toThrow();
});

test("discussion refresh rejects lost or changed public progress and changed visible text", () => {
  const before = discussionTranscript(completedDiscussionSession(), discussionTurns);
  const visible = ["我先核实这一版。", "首轮最终诊断。", "第二轮更短释义。"];
  for (const change of ["missing", "altered", "reordered"]) {
    const after = completedDiscussionSession();
    if (change === "missing") after.entries.splice(1, 1);
    else if (change === "altered") after.entries[1].text = "不同的公开说明。";
    else [after.entries[1], after.entries[2]] = [after.entries[2], after.entries[1]];
    expect(() => assertDiscussionRefresh(before, discussionTranscript(after, discussionTurns), visible, visible)).toThrow();
  }
  expect(() => assertDiscussionRefresh(before, before, visible, visible.slice(1))).toThrow();
  expect(() => assertDiscussionRefresh(before, before, visible, [visible[0], visible[2], visible[1]])).toThrow();
});

test("discussion completion recovers a real session HTTP reset with GET only and records every attempt", async () => {
  const methods: string[] = [];
  const server = createServer((incoming, outgoing) => {
    methods.push(incoming.method ?? "");
    if (methods.length === 1) { incoming.socket.destroy(); return; }
    const session = completedDiscussionSession();
    outgoing.writeHead(200, { "content-type": "application/json" });
    outgoing.end(JSON.stringify(session));
  });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address() as AddressInfo;
  const context = await playwrightRequest.newContext({ baseURL: `http://127.0.0.1:${address.port}` });
  try {
    const browser: Record<string, unknown> = {};
    const transcript = await readCompletedDiscussionTranscript(context, "session-one", discussionTurns, browser);
    expect(transcript.final_replies.map(reply => reply.turn_id)).toEqual(["turn-one", "turn-two"]);
    expect(transcript.public_bodies).toHaveLength(3);
    expect(methods).toEqual(["GET", "GET"]);
    const attempts = browser.observation_retries as Array<{ path: string; recovery: string }>;
    expect(attempts.map(attempt => attempt.path)).toEqual(["/api/agent/sessions/session-one", "/api/agent/sessions/session-one"]);
    expect(attempts.map(attempt => attempt.recovery)).toEqual(["retrying", "recovered"]);
  } finally {
    await context.dispose();
    await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  }
});

test("discussion session recovery rejects unsafe or nested paths before making any GET", async () => {
  let calls = 0;
  const request = { get: async () => { calls++; throw new Error("unexpected GET"); } };
  for (const path of ["/api/agent/sessions/../other", "/api/agent/sessions/id/turns",
    "/api/agent/sessions/id/events", "/api/agent/sessions/id?query=other",
    "/api/agent/sessions/%2e%2e", "http://127.0.0.1/api/agent/sessions/id"]) {
    await expect(readTask(request, path, {})).rejects.toThrow(/仅允许读取已有/);
  }
  expect(calls).toBe(0);
});

const discussionTarget = { run_id: "run-one", revision_id: "revision-one", artifact_digest: "a".repeat(64) };
function delegatedDiscussionEvidence() {
  const records = discussionTurns.map((turn, index) => ({ ...discussionTarget, id: `discussion-${index}`,
    request_id: `request-${index}`, message: `${turn.question}，只诊断可读性，不修改作品。`,
    reply: `第 ${index + 1} 轮真实答复。`, status: "completed" }));
  const delegations = records.map((record, index) => ({ turn_id: discussionTurns[index].turn_id,
    call_id: `call-${index}`, request_snapshot_id: `snapshot-${index}`,
    arguments: { ...discussionTarget, request_id: record.request_id, message: record.message }, result: { ...record } }));
  return { records, delegations };
}

test("discussion records permit delegated phrasing but bind exact call result identity and content", () => {
  const { records, delegations } = delegatedDiscussionEvidence();
  expect(records[0].message).not.toBe(discussionTurns[0].question);
  assertDiscussionRecords(records, delegations, discussionTarget);
  const whitespace = structuredClone(delegations);
  for (const key of ["run_id", "revision_id", "artifact_digest", "request_id", "message"] as const) {
    whitespace[0].arguments[key] = ` ${whitespace[0].arguments[key]} `;
  }
  assertDiscussionRecords(records, whitespace, discussionTarget);
});

test("discussion records reject wrong targets questions identities duplicates dropped or reordered records", () => {
  for (const key of ["run_id", "revision_id", "artifact_digest", "message", "request_id", "reply", "status", "id"] as const) {
    const { records, delegations } = delegatedDiscussionEvidence();
    records[0][key] = "altered";
    expect(() => assertDiscussionRecords(records, delegations, discussionTarget)).toThrow();
  }
  for (const key of ["run_id", "revision_id", "artifact_digest", "message", "request_id"] as const) {
    const { records, delegations } = delegatedDiscussionEvidence();
    delegations[0].arguments[key] = "different tool argument";
    expect(() => assertDiscussionRecords(records, delegations, discussionTarget)).toThrow();
  }
  for (const mode of ["missing", "duplicate", "reverse", "same-turn", "same-call"]) {
    const { records, delegations } = delegatedDiscussionEvidence();
    if (mode === "missing") records.pop();
    if (mode === "duplicate") records[1] = { ...records[0] };
    if (mode === "reverse") records.reverse();
    if (mode === "same-turn") delegations[1].turn_id = delegations[0].turn_id;
    if (mode === "same-call") delegations[1].call_id = delegations[0].call_id;
    expect(() => assertDiscussionRecords(records, delegations, discussionTarget)).toThrow();
  }
  for (const key of ["turn_id", "call_id", "request_snapshot_id"] as const) {
    const { records, delegations } = delegatedDiscussionEvidence();
    delegations[0][key] = "";
    expect(() => assertDiscussionRecords(records, delegations, discussionTarget)).toThrow();
  }
});

test("discussion delegation reads real turn Trace with GET only and rejects incomplete call pairing", async () => {
  const methods: string[] = [];
  const { delegations } = delegatedDiscussionEvidence();
  let mode = "valid";
  const server = createServer((incoming, outgoing) => {
    methods.push(incoming.method ?? "");
    const delegation = delegations[0];
    const result = { tool_call_id: delegation.call_id, name: "discuss_content_run", is_error: false,
      raw_content: JSON.stringify(delegation.result) };
    const snapshot = { turn_id: delegation.turn_id, request_id: "snapshot-0",
      response: { tool_calls: [{ id: delegation.call_id, name: "discuss_content_run", arguments: JSON.stringify(delegation.arguments) }] },
      tool_results: mode === "missing" ? [] : mode === "duplicate" ? [result, result] : [{ ...result,
        tool_call_id: mode === "wrong-call" ? "different-call" : result.tool_call_id,
        is_error: mode === "error", raw_content: mode === "missing-raw" ? undefined : result.raw_content }] };
    if (mode === "wrong-turn") snapshot.turn_id = "different-turn";
    if (mode === "wrong-request") snapshot.request_id = "different-request";
    outgoing.writeHead(200, { "content-type": "application/json" });
    outgoing.end(JSON.stringify(incoming.url?.endsWith("/requests/snapshot-0") ? snapshot : {
      turn_id: delegation.turn_id, available: true, requests: [{ request_id: "snapshot-0", snapshot_available: true }] }));
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const context = await playwrightRequest.newContext({ baseURL: `http://127.0.0.1:${(server.address() as AddressInfo).port}` });
  try {
    expect(await readDiscussionDelegation(context, "session-one", "turn-one")).toEqual(delegations[0]);
    for (mode of ["missing", "duplicate", "wrong-call", "error", "missing-raw", "wrong-turn", "wrong-request"]) {
      await expect(readDiscussionDelegation(context, "session-one", "turn-one")).rejects.toThrow();
    }
    expect(methods).toHaveLength(16);
    expect(methods.every(method => method === "GET")).toBe(true);
  } finally {
    await context.dispose();
    await new Promise<void>(resolve => server.close(() => resolve()));
  }
});

test("image observer waits beyond five seconds for real HTTP DOM and decoding, and rejects bad evidence", async ({ page }) => {
  const methods: string[] = [];
  const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=", "base64");
  let mode = "delayed-dom";
  let imageGets = 0;
  const server = createServer((incoming, outgoing) => {
    methods.push(incoming.method ?? "");
    if (incoming.url === "/") {
      outgoing.writeHead(200, { "content-type": "text/html" });
      outgoing.end(mode === "delayed-dom"
        ? '<script>fetch("/fragment").then(r=>r.text()).then(html=>document.body.innerHTML=html)</script>'
        : '<img src="/image.png">');
    } else if (incoming.url === "/fragment") {
      setTimeout(() => { outgoing.writeHead(200, { "content-type": "text/html" }); outgoing.end('<img src="/image.png">'); }, 6_000);
    } else {
      imageGets++;
      const send = () => { outgoing.writeHead(mode === "non200" && imageGets > 1 ? 503 : 200,
        { "content-type": "image/png", "cache-control": "no-store" }); outgoing.end(mode === "corrupt" ? "bad png" : png); };
      if (mode === "delayed-image" && imageGets === 1) setTimeout(send, 6_000); else send();
    }
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const url = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const context = await playwrightRequest.newContext();
  try {
    for (mode of ["delayed-dom", "delayed-image"]) {
      imageGets = 0;
      await page.goto(url, { waitUntil: "domcontentloaded" });
      const observed = await imageInfo(page.locator("img"), context);
      expect(observed.width).toBe(1);
      expect(observed.height).toBe(1);
      expect(() => expect(observed.sha256).toBe("0".repeat(64))).toThrow();
    }
    for (mode of ["corrupt", "non200"]) {
      imageGets = 0;
      await page.goto(url, { waitUntil: "domcontentloaded" });
      await expect(imageInfo(page.locator("img"), context, 500)).rejects.toThrow();
    }
    expect(methods.every(method => method === "GET")).toBe(true);
  } finally {
    await context.dispose();
    await new Promise<void>(resolve => server.close(() => resolve()));
  }
});
