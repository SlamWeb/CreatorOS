import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { createHash } from "node:crypto";

// Valid PNGs. Model execution is the isolated server's explicit deterministic fixture.
const first = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAACAAAAAwCAIAAAD/zu84AAAAR0lEQVR4nO3RMRHAMAwEQTvo0oeG+deBcG7U7QJ4zZz2+5016RldXw5c8IMkUZIoSZQkShIliZJESaIkUZIoSZQkShKl8UQ/KZgBZ563c68AAAAASUVORK5CYII=", "base64");
const second = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAACAAAAAwCAIAAAD/zu84AAAAR0lEQVR4nO3RMRHAMAwEQTuoAydQDC0Qzo26XQCvmdN+v7MmPaPry4ELfpAkShIliZJESaIkUZIoSZQkShIliZJESaI0nugHAeMCOArj8qgAAAAASUVORK5CYII=", "base64");
const pictures = [{ name: "选题参考一.png", mimeType: "image/png", buffer: first }, { name: "选题参考二.png", mimeType: "image/png", buffer: second }];
async function fixture(request: APIRequestContext) {
  const post = async (path: string, data: object) => {
    const result = await request.post(path, { data });
    expect(result.ok(), await result.text()).toBe(true);
    return result.json();
  };
  const creator = await post("/api/creators", { display_name: "参考选题隔离账号" });
  const series = await post(`/api/creators/${creator.id}/series`, { name: "参考创作测试栏目" });
  return { creator, series };
}
async function open(page: Page, seriesId: string) {
  await page.goto(`/series/${seriesId}`);
  const form = page.locator(".topic-reference-composer");
  if (!await form.isVisible()) await page.getByRole("button", { name: "调研选题", exact: true }).click();
  await expect(form).toBeVisible();
  return form;
}

test("reference picker multi-select, drag, remove and constraints preserve draft without starting tasks", async ({ page, request }) => {
  const { series } = await fixture(request);
  const form = await open(page, series.id);
  const writes: string[] = [];
  page.on("request", req => { if (req.method() === "POST") writes.push(req.url()); });
  await form.getByLabel("参考图片", { exact: true }).setInputFiles(pictures);
  await expect(form.getByLabel("已选图片").getByRole("img")).toHaveCount(2);
  await expect(form.getByRole("checkbox", { name: /同时参考呈现/ })).not.toBeChecked();
  await form.getByRole("button", { name: "直接制作", exact: true }).click();
  await expect(form.getByRole("alert")).toContainText("写下这次想制作的内容");
  await form.getByLabel("本次偏好").fill("保留这个选题切入点，不照搬角色。");
  await form.getByRole("button", { name: "移除 选题参考二.png", exact: true }).click();
  const transfer = await page.evaluateHandle(({ bytes }) => {
    const data = new DataTransfer();
    data.items.add(new File([new Uint8Array(bytes)], "拖入参考.png", { type: "image/png" }));
    return data;
  }, { bytes: [...second] });
  await form.getByRole("group", { name: "参考图片上传区" }).dispatchEvent("drop", { dataTransfer: transfer });
  await expect(form.getByLabel("已选图片").getByRole("img")).toHaveCount(2);
  await transfer.dispose();
  await form.getByLabel("参考图片", { exact: true }).setInputFiles({ name: "不支持.txt", mimeType: "text/plain", buffer: Buffer.from("not an image") });
  await expect(form.getByRole("alert")).toContainText("请选择 PNG");
  await expect(form.getByLabel("已选图片").getByRole("img")).toHaveCount(2);
  await form.getByLabel("参考图片", { exact: true }).setInputFiles(Array.from({ length: 19 }, (_, index) => ({ ...pictures[0], name: `额外-${index}.png` })));
  await expect(form.getByRole("alert")).toContainText("最多 20 张");
  await page.getByRole("button", { name: "调研选题", exact: true }).click();
  await expect(form).toBeHidden();
  await page.getByRole("button", { name: "调研选题", exact: true }).click();
  await expect(form.getByLabel("本次偏好")).toHaveValue("保留这个选题切入点，不照搬角色。");
  await expect(form.getByLabel("已选图片").getByRole("img")).toHaveCount(2);
  expect(writes).toEqual([]);
});

test("expand topics freezes original pictures and full intent but creates no production run", async ({ page, request }) => {
  const { series } = await fixture(request);
  const form = await open(page, series.id);
  const sent: Array<{ path: string; body: Record<string, unknown> }> = [];
  page.on("request", req => { if (req.method() === "POST") sent.push({ path: new URL(req.url()).pathname, body: req.postDataJSON() }); });
  await form.getByLabel("参考图片", { exact: true }).setInputFiles([...pictures, { ...pictures[0], name: "相同内容不同名.png" }]);
  await form.getByLabel("本次偏好").fill("借鉴图片中用日常小事讲明白概念的切入点，扩展为常考英语选题；不要改变栏目IP。");
  await form.getByLabel("候选数量").fill("2");
  await form.getByRole("button", { name: "扩展选题", exact: true }).click();
  await expect(page.getByRole("heading", { name: "选择本批次选题" })).toBeVisible();
  await expect(page.locator(".research-card")).toHaveCount(2);
  const freeze = sent.find(item => item.path.endsWith("/reference-inputs"))!;
  expect(freeze.body.reference_presentation).toBe(false);
  expect(freeze.body.image_ids).toHaveLength(2);
  expect(freeze.body.instructions).toContain("不要改变栏目IP");
  const submission = sent.find(item => item.path.endsWith("/topic-research"))!;
  expect(submission.body.reference_id).toMatch(/^[a-f0-9]{64}$/);
  expect(submission.body.instructions).toBe(freeze.body.instructions);
  expect(sent.some(item => item.path.endsWith("/reference-production") || item.path === "/api/runs")).toBe(false);
  expect((await request.get(`/api/series/${series.id}/topics`).then(r => r.json())).page.total).toBe(0);
  await page.reload();
  await expect(page.locator(".research-card")).toHaveCount(2);
  expect(sent.filter(item => item.path.endsWith("/topic-research"))).toHaveLength(1);
});

test("direct production passes originals and complete requirements to one run without candidate regeneration", async ({ page, request }, info) => {
  const { series } = await fixture(request);
  const form = await open(page, series.id);
  const sent: Array<{ path: string; body: Record<string, unknown> }> = [];
  page.on("request", req => { if (req.method() === "POST") sent.push({ path: new URL(req.url()).pathname, body: req.postDataJSON() }); });
  const instructions = "直接借鉴两张图的选题，讲解四个常考易混英语词，保留中英文释义。这次允许参考漫画呈现，但不要替换栏目角色。";
  await form.getByLabel("参考图片", { exact: true }).setInputFiles(pictures);
  await form.getByLabel("本次偏好").fill(instructions);
  await form.getByRole("checkbox", { name: /同时参考呈现/ }).check();
  await page.screenshot({ path: info.outputPath("references-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(form.getByLabel("参考图片", { exact: true })).toHaveCSS("width", "1px");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("references-mobile.png"), fullPage: true });
  await form.getByRole("button", { name: "直接制作", exact: true }).click();
  await expect(page).toHaveURL(/\/runs\/[^/?]+$/);
  const runId = new URL(page.url()).pathname.split("/").at(-1)!;
  await expect.poll(async () => (await request.get(`/api/runs/${runId}`).then(r => r.json())).status).toBe("awaiting_approval");
  const evidenceResponse = await request.get(`/test/topic-references/${runId}`);
  expect(evidenceResponse.ok(), await evidenceResponse.text()).toBe(true);
  expect(evidenceResponse.headers()["content-type"]).toContain("application/json");
  const evidence = await evidenceResponse.json();
  expect(JSON.stringify(evidence)).toContain(instructions);
  for (const bytes of [first, second]) expect(JSON.stringify(evidence)).toContain(createHash("sha256").update(bytes).digest("hex"));
  const run = await request.get(`/api/runs/${runId}`).then(r => r.json());
  expect(run.input_snapshot.task_references.reference_presentation).toBe(true);
  expect(run.input_snapshot.task_references.images).toHaveLength(2);
  expect(sent.filter(item => item.path.endsWith("/reference-production"))).toHaveLength(1);
  expect(sent.some(item => item.path.endsWith("/topic-research"))).toBe(false);
  expect((await request.get(`/api/series/${series.id}/topics`).then(r => r.json())).page.total).toBe(1);
  const before = sent.length;
  await page.reload();
  await expect(page.getByRole("button", { name: "下载全部（3）", exact: true })).toBeVisible();
  expect(sent).toHaveLength(before);
});

test("lost direct response preserves requirements and retries the same key without another production", async ({ page, request }) => {
  const { series } = await fixture(request);
  const form = await open(page, series.id);
  let uploads = 0;
  const keys: string[] = [];
  let runId = "";
  page.on("request", req => { if (req.method() === "POST" && new URL(req.url()).pathname.endsWith("/reference-images")) uploads++; });
  await page.route("**/reference-production", async route => {
    keys.push(route.request().postDataJSON().request_id);
    const response = await route.fetch();
    expect(response.ok(), await response.text()).toBe(true);
    runId = (await response.json()).id;
    if (keys.length === 1) await route.abort("failed");
    else await route.fulfill({ response });
  });
  await form.getByLabel("参考图片", { exact: true }).setInputFiles(pictures);
  await form.getByLabel("本次偏好").fill("用参考选题直接制作一篇新内容，不复现原图。");
  await form.getByRole("button", { name: "直接制作", exact: true }).click();
  await expect(form.getByRole("alert")).toContainText("图片和要求已保留");
  await expect(form.getByLabel("本次偏好")).toHaveValue("用参考选题直接制作一篇新内容，不复现原图。");
  expect(keys).toHaveLength(1);
  expect(uploads).toBe(2);
  await form.getByRole("button", { name: "直接制作", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/runs/${runId}$`));
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
  expect(uploads).toBe(2);
  expect((await request.get(`/api/series/${series.id}/topics`).then(r => r.json())).page.total).toBe(1);
  await expect.poll(async () => (await request.get(`/api/runs/${runId}`).then(r => r.json())).status).toBe("awaiting_approval");
  const run = await request.get(`/api/runs/${runId}`).then(r => r.json());
  expect(run.revisions).toHaveLength(1);
  expect(run.revisions[0].attempts).toHaveLength(1);
  await page.unrouteAll({ behavior: "wait" });
});
