import { expect, test, type APIRequestContext } from "@playwright/test";
import { createServer, type ServerResponse, type IncomingMessage } from "node:http";
import { readProgress, type ObservationAttempt } from "../live-workbench/read-progress";

const path = "/api/runs/existing-run";

async function localServer(handler: (request: IncomingMessage, response: ServerResponse) => void) {
  const server = createServer(handler);
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("No local test port.");
  return { url: `http://127.0.0.1:${address.port}`, async close() {
    server.closeAllConnections();
    await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  } };
}

function reply(response: ServerResponse, status: number, body: string) {
  response.writeHead(status, { "content-type": "application/json" });
  response.end(body);
}

function records(browser: Record<string, unknown>) {
  return browser.observation_retries as ObservationAttempt[];
}

test("three real socket resets recover on fourth GET and keep all evidence", async ({ playwright }) => {
  const requests: Array<{ method?: string; url?: string }> = [];
  const host = await localServer((request, response) => {
    requests.push({ method: request.method, url: request.url });
    if (requests.length <= 3) request.socket.destroy();
    else reply(response, 200, '{"status":"producing"}');
  });
  const client = await playwright.request.newContext({ baseURL: host.url });
  const browser: Record<string, unknown> = {};
  try {
    expect(await readProgress(client, path, browser)).toEqual({ status: "producing" });
    expect(requests).toEqual(Array.from({ length: 4 }, () => ({ method: "GET", url: path })));
    expect(records(browser).map(row => row.attempt)).toEqual([1, 2, 3, 4]);
    expect(records(browser).map(row => row.recovery)).toEqual(["retrying", "retrying", "retrying", "recovered"]);
    expect(records(browser).slice(0, 3).every(row => /ECONNRESET|socket hang up/i.test(row.error ?? ""))).toBe(true);
    expect(records(browser).every(row => row.path === path && Number.isFinite(Date.parse(row.time)))).toBe(true);
    expect(records(browser)[3]).toMatchObject({ error: null, http_status: 200, business_status: "producing" });
  } finally { await client.dispose(); await host.close(); }
});

test("persistent socket resets stop after exactly four actual GETs", async ({ playwright }) => {
  let attempts = 0;
  const host = await localServer(request => { attempts++; request.socket.destroy(); });
  const client = await playwright.request.newContext({ baseURL: host.url });
  const browser: Record<string, unknown> = {};
  try {
    await expect(readProgress(client, path, browser)).rejects.toThrow(/ECONNRESET|socket hang up/i);
    expect(attempts).toBe(4);
    expect(records(browser)).toHaveLength(4);
    expect(records(browser).at(-1)?.recovery).toBe("exhausted");
  } finally { await client.dispose(); await host.close(); }
});

for (const sample of [
  { name: "HTTP failure even with reset text", http: 503, body: '{"error":"ECONNRESET socket hang up"}', error: /HTTP 503/ },
  { name: "invalid JSON even with reset text", http: 200, body: "ECONNRESET socket hang up", error: /JSON|Unexpected token|unexpected character/i },
  { name: "missing business status", http: 200, body: '{"unexpected":"ECONNRESET"}', error: /status/ },
]) {
  test(`${sample.name} is not retried`, async ({ playwright }) => {
    let attempts = 0;
    const host = await localServer((_request, response) => { attempts++; reply(response, sample.http, sample.body); });
    const client = await playwright.request.newContext({ baseURL: host.url });
    const browser: Record<string, unknown> = {};
    try {
      await expect(readProgress(client, path, browser)).rejects.toThrow(sample.error);
      expect(attempts).toBe(1);
      expect(records(browser)).toHaveLength(1);
      expect(records(browser)[0].recovery).toBe("not_retryable");
    } finally { await client.dispose(); await host.close(); }
  });
}

for (const status of ["ready", "awaiting_approval", "failed", "unknown", "interrupted", "cancelled"]) {
  test(`${status} business state returns after one GET without restarting`, async ({ playwright }) => {
    let attempts = 0;
    const host = await localServer((_request, response) => { attempts++; reply(response, 200, JSON.stringify({ status })); });
    const client = await playwright.request.newContext({ baseURL: host.url });
    const browser: Record<string, unknown> = {};
    try {
      expect(await readProgress(client, path, browser)).toEqual({ status });
      expect(attempts).toBe(1);
      expect(records(browser)[0]).toMatchObject({ error: null, business_status: status, recovery: "not_needed" });
    } finally { await client.dispose(); await host.close(); }
  });
}

test("non-reset transport errors and paths outside existing task GETs are not retried", async ({ playwright }) => {
  const host = await localServer((_request, response) => reply(response, 200, '{"status":"ready"}'));
  const client: APIRequestContext = await playwright.request.newContext({ baseURL: host.url });
  await host.close(); // Real loopback connection refusal, not a mocked SDK/LLM failure.
  const browser: Record<string, unknown> = {};
  try {
    await expect(readProgress(client, path, browser)).rejects.toThrow(/ECONNREFUSED/);
    expect(records(browser)).toHaveLength(1);
    expect(records(browser)[0].recovery).toBe("not_retryable");
    await expect(readProgress(client, "/api/runs/existing-run/start", browser)).rejects.toThrow(/已有/);
    expect(records(browser)).toHaveLength(1);
    await expect(readProgress(client, "/__live_eval__/finish", browser)).rejects.toThrow(/已有/);
    expect(records(browser)).toHaveLength(1);
  } finally { await client.dispose(); }
});
