import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";

// API-only preparation proof; never launches a browser or submits an Agent turn.
test("prepared production fixture is isolated, empty, and forbids production writes", async ({ request }) => {
  const setup = JSON.parse(readFileSync(path.join(process.env.CREATOROS_TASKS_LIVE_ROOT!, "setup.json"), "utf8"));
  const response = await request.get("/test/production-tasks-fixture");
  expect(response.ok()).toBeTruthy();
  const fixture = await response.json();
  expect(fixture.fixture_id).toBe(setup.fixture_id);
  expect(fixture.root).toBe(setup.root);
  expect(fixture.mode).toBe("prepare-only");
  expect(fixture.workers_started).toBe(false);
  expect((await (await request.get(`/api/topics/${setup.topic_id}/production-tasks`)).json()).items).toEqual([]);
  expect((await request.post("/api/agent/sessions", { data: {} })).status()).toBe(403);
});
