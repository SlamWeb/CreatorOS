import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { expect, request as playwrightRequest, test } from "@playwright/test";
import { preservePrimaryFailure } from "../live-workbench/preserve-primary-failure";
import { readTask } from "../live-workbench/read-progress";
import { sentDiscussionQueries } from "../live-workbench/discussion-rework-journey";

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
