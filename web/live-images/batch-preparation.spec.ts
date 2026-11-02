import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";

test("new batch fixture is prepared without any model or production worker", async ({ request }) => {
  const setup = JSON.parse(readFileSync(path.join(process.env.CREATOROS_BATCH_IMAGES_ROOT!, "batch-setup.json"), "utf8"));
  const response = await request.get("/test/batch/fixture");
  expect(response.ok()).toBeTruthy();
  const fixture = await response.json();
  expect(fixture.fixture_id).toBe(setup.fixture_id);
  expect(fixture.live_models).toBe(false);
  expect(fixture.background_services_started).toBe(false);
  expect(fixture.submitted).toBe(false);
  expect((await (await request.get("/api/skill-extractions")).json()).items).toEqual([]);
  expect((await request.post("/api/runs", { data: {} })).status()).toBe(403);
});
