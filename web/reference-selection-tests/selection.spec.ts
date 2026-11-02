import { test, expect, type Page, type APIRequestContext } from "@playwright/test";
import { createHash } from "node:crypto";

const image = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAACAAAAAwCAIAAAD/zu84AAAAR0lEQVR4nO3RMRHAMAwEQTvo0oeG+deBcG7U7QJ4zZz2+5016RldXw5c8IMkUZIoSZQkShIliZJESaIkUZIoSZQkShKl8UQ/KZgBZ563c68AAAAASUVORK5CYII=", "base64");
async function setup(page: Page, request: APIRequestContext) {
  const response = await request.post("/test/reference-selection", { data: {} });
  expect(response.ok(), await response.text()).toBe(true);
  const fixture = await response.json();
  await page.goto(`/series/${fixture.series}`);
  await page.getByRole("button", { name: "调研选题", exact: true }).click();
  const form = page.locator(".topic-reference-composer");
  await form.getByLabel("参考图片", { exact: true }).setInputFiles({ name: "original.png", mimeType: "image/png", buffer: image });
  await form.getByLabel("本次偏好").fill("完整保留原图与本次要求，仅借鉴内容。");
  await expect(form.locator(".topic-reference-skill-options input")).toHaveCount(2);
  return { fixture, form };
}

test("choose multiple exact column Skills, freeze originals, and refresh without another production", async ({ page, request }, info) => {
  const { fixture, form } = await setup(page, request);
  await form.getByRole("button", { name: "直接制作", exact: true }).click();
  await expect(form.getByRole("alert")).toContainText("勾选本次使用的版本");
  for (const checkbox of await form.locator(".topic-reference-skill-options input").all()) await checkbox.check();
  await page.screenshot({ path: info.outputPath("selection-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("selection-mobile.png"), fullPage: true });
  const writes: object[] = [];
  page.on("request", req => { if (req.url().endsWith("/reference-production")) writes.push(req.postDataJSON()); });
  await form.getByRole("button", { name: "直接制作", exact: true }).click();
  await expect(page).toHaveURL(/\/runs\/[^/?]+$/);
  const runId = new URL(page.url()).pathname.split("/").at(-1)!;
  await expect.poll(async () => (await request.get(`/api/runs/${runId}`).then(r => r.json())).status, { timeout: 30_000 }).toBe("awaiting_approval");
  const run = await request.get(`/api/runs/${runId}`).then(r => r.json());
  expect(run.input_snapshot.execution_spec.skills.map((skill: { skill_id: string }) => skill.skill_id)).toEqual(fixture.skills.map((skill: { id: string }) => skill.id));
  expect(run.input_snapshot.task_references.instructions).toBe("完整保留原图与本次要求，仅借鉴内容。");
  expect(JSON.stringify(run.input_snapshot.task_references)).toContain(createHash("sha256").update(image).digest("hex"));
  expect((await request.get(`/api/series/${fixture.series}/skills`).then(r => r.json())).version).toBe(fixture.pool.version);
  expect((await request.get(`/api/series/${fixture.series}/topics`).then(r => r.json())).page.total).toBe(1);
  await page.reload();
  await expect(page.getByRole("button", { name: "下载全部（3）", exact: true })).toBeVisible();
  expect(writes).toHaveLength(1);
});

test("lost response retains selected versions; changed selection conflicts and exact retry returns the accepted Run", async ({ page, request }) => {
  const { fixture, form } = await setup(page, request);
  const checkboxes = form.locator(".topic-reference-skill-options input");
  await checkboxes.nth(0).check();
  let accepted: { id: string } | undefined;
  let firstBody: Record<string, unknown> | undefined;
  await page.route("**/reference-production", async route => {
    firstBody = route.request().postDataJSON();
    const result = await route.fetch();
    expect(result.status()).toBe(202);
    accepted = await result.json();
    await route.abort("failed");
  }, { times: 1 });
  await form.getByRole("button", { name: "直接制作", exact: true }).click();
  await expect(form.getByRole("alert")).toContainText("结果未知");
  await expect(checkboxes.nth(0)).toBeChecked();
  await checkboxes.nth(1).check();
  const conflict = page.waitForResponse(response => response.url().endsWith("/reference-production") && response.status() === 409);
  await form.getByRole("button", { name: "直接制作", exact: true }).click();
  const changed = await conflict;
  expect(changed.request().postDataJSON().request_id).toBe(firstBody!.request_id);
  await expect(form.getByRole("alert")).toContainText("不同的 Skill");
  await checkboxes.nth(1).uncheck();
  await form.getByRole("button", { name: "直接制作", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/runs/${accepted!.id}$`));
  expect((await request.get(`/api/series/${fixture.series}/topics`).then(r => r.json())).page.total).toBe(1);
  const evidence = await request.get(`/test/reference-selection/${fixture.series}`).then(r => r.json());
  expect(evidence.runs).toEqual([accepted!.id]);
  expect(evidence.attempt_count).toBe(1);
  expect(evidence.queue_count).toBe(1);
});
