import { expect, test } from "@playwright/test";

// API-only: this also exercises config loading in a Playwright worker without
// launching a browser or claiming that fixture images came from a model.
test("isolated media fixture exposes two validated image runs", async ({ request }) => {
  const fixture = await (await request.get("/test/media-fixture")).json();
  expect(fixture.workers_started).toBe(false);
  expect(fixture.run_ids).toHaveLength(2);
  expect(new Set(fixture.run_ids).size).toBe(2);
  for (const id of fixture.run_ids) {
    const response = await request.get(`/api/runs/${id}`);
    expect(response.ok(), await response.text()).toBeTruthy();
    const run = await response.json();
    expect(run.status).toBe("awaiting_approval");
    expect(run.revisions[0].artifact_available).toBe(true);
    expect(run.revisions[0].cards.length).toBeGreaterThan(0);
  }
  expect(fixture.source_run_rows_unchanged).toBe(true);
  expect(fixture.source_images_unchanged).toBe(true);
});
