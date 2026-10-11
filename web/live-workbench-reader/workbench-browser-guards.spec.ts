import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { expect, request as playwrightRequest, test } from "@playwright/test";
import { preservePrimaryFailure } from "../live-workbench/preserve-primary-failure";
import { readTask } from "../live-workbench/read-progress";

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
