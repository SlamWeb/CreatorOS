import { expect, test, type APIRequestContext } from "@playwright/test";

const label = process.env.CREATOROS_ISOLATION_LABEL!;
const other = process.env.CREATOROS_ISOLATION_OTHER_URL!;
const peer = label === "a" ? "b" : "a";
const png = "iVBORw0KGgoAAAANSUhEUgAAACAAAAAwCAIAAAD/zu84AAAAR0lEQVR4nO3RMRHAMAwEQTvo0oeG+deBcG7U7QJ4zZz2+5016RldXw5c8IMkUZIoSZQkShIliZJESaIkUZIoSZQkShKl8UQ/KZgBZ563c68AAAAASUVORK5CYII=";

async function post(request: APIRequestContext, path: string, data: object) {
  const response = await request.post(path, { data });
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}

test("browser, API data, session files and controlled outputs stay in their instance", async ({ page, request, context }, info) => {
  const account = await post(request, "/api/creators", { display_name: `isolation-${label}` });
  const session = await post(request, "/api/agent/sessions", { creator_id: account.id });
  await post(request, `/api/agent/sessions/${session.id}/attachments`, { name: `${label}.png`, data_base64: png });
  const series = (await post(request, "/api/series", { creator_id: account.id, name: `series-${label}`,
    skill_name: "knowledge-to-carousel", request_id: `series-${label}` })).series;
  const queue = await post(request, `/api/series/${series.id}/queue`,
    { request_id: `isolation-${label}`, topics: [{ title: `topic-${label}` }] });
  const run = await post(request, "/api/runs", { topic_id: queue.topic_ids[0] });
  await post(request, `/api/runs/${run.id}/execute`, { expected_version: run.version });
  await expect.poll(async () => (await (await request.get(`/api/runs/${run.id}`)).json()).status,
    { timeout: 30_000 }).toBe("awaiting_approval");

  await page.goto(`/?creator=${account.id}`);
  await expect(page.getByRole("heading", { name: account.display_name })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: account.display_name })).toBeVisible();
  await page.evaluate(() => localStorage.setItem("isolation-check", "own"));
  const otherPage = await context.newPage();
  await otherPage.goto(other);
  expect(await otherPage.evaluate(() => localStorage.getItem("isolation-check"))).toBeNull();
  await otherPage.close();

  await expect.poll(async () => {
    const body = await (await request.get(`${other}/api/creators`)).json();
    return body.items.some((item: { display_name: string }) => item.display_name === `isolation-${peer}`);
  }, { timeout: 30_000 }).toBe(true);
  const ours = await (await request.get("/api/creators")).json();
  const theirs = await (await request.get(`${other}/api/creators`)).json();
  expect(ours.items.map((item: { display_name: string }) => item.display_name)).toContain(`isolation-${label}`);
  expect(ours.items.map((item: { display_name: string }) => item.display_name)).not.toContain(`isolation-${peer}`);
  expect(theirs.items.map((item: { display_name: string }) => item.display_name)).not.toContain(`isolation-${label}`);
  expect((await request.get(`${other}/api/agent/sessions/${session.id}`)).status()).toBe(404);
  await page.screenshot({ path: info.outputPath("instance.png") });
});
