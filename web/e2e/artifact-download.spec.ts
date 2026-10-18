import { expect, test, type APIRequestContext } from "@playwright/test";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";

// A valid Pillow-generated 32×48 PNG, not the old upload-mocking placeholder.
const png = "iVBORw0KGgoAAAANSUhEUgAAACAAAAAwCAIAAAD/zu84AAAAR0lEQVR4nO3RMRHAMAwEQTvo0oeG+deBcG7U7QJ4zZz2+5016RldXw5c8IMkUZIoSZQkShIliZJESaIkUZIoSZQkShKl8UQ/KZgBZ563c68AAAAASUVORK5CYII=";
const hash = (bytes: Buffer) => createHash("sha256").update(bytes).digest("hex");
async function post(request: APIRequestContext, path: string, data: object) {
  const response = await request.post(path, { data });
  expect(response.ok(), await response.text()).toBe(true);
  return response.json();
}
async function setup(request: APIRequestContext, title: string, single = false) {
  const creator = await post(request, "/api/creators", { display_name: "图片下载隔离账号" });
  const series = await post(request, `/api/creators/${creator.id}/series`, { name: "下载测试" });
  if (single) {
    const session = await post(request, "/api/agent/sessions", { creator_id: creator.id });
    const image = await post(request, `/api/agent/sessions/${session.id}/attachments`, { name: "原图.png", data_base64: png });
    return post(request, `/api/agent/sessions/${session.id}/image-actions/import`, {
      request_id: crypto.randomUUID(), series_id: series.id, attachment_ids: [image.id], title, body: "隔离原图下载测试，不调用模型。",
    }).then(async imported => request.get(`/api/runs/${imported.run_id}`).then(r => r.json()));
  }
  const topics = await post(request, `/api/series/${series.id}/queue`, { request_id: crypto.randomUUID(), topics: [{ title }] });
  const run = await post(request, "/api/runs", { topic_id: topics.topic_ids[0] });
  await post(request, `/api/runs/${run.id}/execute`, { expected_version: run.version });
  await expect.poll(async () => (await request.get(`/api/runs/${run.id}`).then(r => r.json())).status).toBe("awaiting_approval");
  return request.get(`/api/runs/${run.id}`).then(r => r.json());
}

test("single image downloads original PNG before approval; refresh has no writes", async ({ page, request }, info) => {
  const run = await setup(request, "单张 / 原图", true);
  const writes: string[] = [];
  page.on("request", req => { if (req.method() !== "GET") writes.push(req.url()); });
  await page.goto(`/runs/${run.id}`);
  const result = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载图片", exact: true }).click();
  const download = await result;
  expect(await download.failure()).toBeNull();
  expect(download.suggestedFilename()).toBe("单张 _ 原图-第1版.png");
  expect(readFileSync((await download.path())!)).toEqual(Buffer.from(png, "base64"));
  await expect(page.getByRole("button", { name: "批准第 1 版" })).toBeVisible();
  await page.screenshot({ path: info.outputPath("single-desktop.png") });
  await page.reload();
  await expect(page.getByRole("button", { name: "下载图片", exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: info.outputPath("single-mobile.png") });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(writes).toEqual([]);
  const after = await request.get(`/api/runs/${run.id}`).then(r => r.json());
  expect(after).toEqual(run);
});

test("multi image downloads ordered ZIP and selected history without mutation", async ({ page, request }, info) => {
  const run = await setup(request, "多图下载");
  const old = run.revisions[0];
  await post(request, `/api/runs/${run.id}/revisions`, { expected_version: run.version, instruction: "隔离测试第二版" });
  const queued = await request.get(`/api/runs/${run.id}`).then(r => r.json());
  await post(request, `/api/runs/${run.id}/execute`, { expected_version: queued.version });
  await expect.poll(async () => (await request.get(`/api/runs/${run.id}`).then(r => r.json())).status).toBe("awaiting_approval");
  const before = await request.get(`/api/runs/${run.id}`).then(r => r.json());
  const writes: string[] = [], downloads: string[] = [];
  page.on("request", req => {
    if (req.method() !== "GET") writes.push(req.url());
    if (new URL(req.url()).pathname.endsWith("/download")) downloads.push(req.url());
  });
  await page.goto(`/runs/${run.id}`);
  await page.getByLabel("内容版本").selectOption(old.id);
  const result = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载全部（3）", exact: true }).click();
  const download = await result;
  expect(await download.failure()).toBeNull();
  expect(download.suggestedFilename()).toBe("多图下载-第1版.zip");
  const script = "import sys,zipfile,json,hashlib; z=zipfile.ZipFile(sys.argv[1]); print(json.dumps({n:hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist()}))";
  const entries = JSON.parse(execFileSync(process.env.CREATOROS_PYTHON ?? "python", ["-c", script, (await download.path())!], { encoding: "utf8" }));
  expect(Object.keys(entries)).toEqual(["01.png", "02.png", "03.png"]);
  for (const card of old.cards) expect(entries[`${String(card.order).padStart(2, "0")}.png`]).toBe(hash(await request.get(card.url).then(r => r.body())));
  expect(downloads).toHaveLength(1);
  expect(downloads[0]).toContain(`/revisions/${old.id}/download?digest=${old.review_digest}`);
  await page.getByLabel("内容版本").selectOption(before.revisions[1].id);
  const currentResult = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载全部（3）", exact: true }).click();
  expect((await currentResult).suggestedFilename()).toBe("多图下载-第2版.zip");
  expect(downloads[1]).toContain(`/revisions/${before.revisions[1].id}/download`);
  await page.screenshot({ path: info.outputPath("multi-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: info.outputPath("multi-mobile.png") });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(writes).toEqual([]);
  expect(await request.get(`/api/runs/${run.id}`).then(r => r.json())).toEqual(before);
});

test("busy download rejects repeat clicks; errors stay inline and retry is explicit", async ({ page, request }) => {
  const run = await setup(request, "下载错误恢复", true);
  let release!: () => void;
  const wait = new Promise<void>(resolve => { release = resolve; });
  let attempts = 0, downloads = 0;
  page.on("download", () => { downloads++; });
  await page.route("**/revisions/*/download?*", async route => {
    attempts++;
    if (attempts === 1) {
      await wait;
      return route.fulfill({ status: 409, json: { error: { message: "图片已变化，请刷新详情后重试下载。" } } });
    }
    return route.continue();
  });
  await page.goto(`/runs/${run.id}`);
  await page.getByRole("button", { name: "下载图片", exact: true }).click();
  await expect(page.getByRole("button", { name: "正在准备…", exact: true })).toBeDisabled();
  expect(attempts).toBe(1);
  release();
  await expect(page.getByRole("alert")).toContainText("图片已变化");
  expect(downloads).toBe(0);
  await expect(page).toHaveURL(new RegExp(`/runs/${run.id}$`));
  const result = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载图片", exact: true }).click();
  expect(await (await result).failure()).toBeNull();
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(attempts).toBe(2);
  expect(downloads).toBe(1);
});
