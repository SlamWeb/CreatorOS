import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";

type Fixture = { creator_id: string; run_ids: string[]; topics: { id: string; title: string }[];
  uses_real_artifacts: boolean; workers_started: boolean;
  source_run_rows_unchanged: boolean; source_images_unchanged: boolean };
type Detail = { id: string; name: string; version: number; snapshot_digest: string;
  items: { id: string; included: boolean; run_id: string; checksum: string; url: string }[] };

test("real existing images: cross-Run folder, selection, order, preview, ZIP and refresh", async ({ page, request }, info) => {
  const fixture: Fixture = await request.get("/test/media-fixture").then(r => r.json());
  expect(fixture.uses_real_artifacts).toBe(true);
  expect(fixture.workers_started).toBe(false);
  const base = `/api/creators/${fixture.creator_id}/collections`;
  const writes: string[] = [];
  page.on("request", r => { if (!["GET", "HEAD"].includes(r.method())) writes.push(new URL(r.url()).pathname); });
  await page.goto(`/?creator=${fixture.creator_id}&view=materials`);
  await expect(page.getByRole("heading", { name: "素材文件夹", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "新建文件夹", exact: true }).first().click();
  const form = page.locator(".media-create");
  await form.getByLabel("文件夹名称", { exact: true }).fill("坚持系列 · 跨任务收图验收");
  await form.getByRole("combobox").selectOption(fixture.topics[0].id);
  await form.locator(".media-run-options label").filter({ hasText: fixture.topics[1].title }).first().getByRole("checkbox").check();
  await form.getByRole("button", { name: "创建文件夹", exact: true }).click();
  await expect(form).toHaveCount(0);
  await expect.poll(() => new URL(page.url()).searchParams.get("collection")).toBeTruthy();
  const id = new URL(page.url()).searchParams.get("collection")!;
  const get = () => request.get(`${base}/${id}`).then(r => r.json() as Promise<Detail>);
  const first = await get();
  expect(new Set(first.items.map(item => item.run_id))).toEqual(new Set(fixture.run_ids));
  expect(first.items.length).toBeGreaterThanOrEqual(2);
  await expect(page.locator(".media-image-grid article")).toHaveCount(first.items.length);
  const firstImage = page.locator(".media-image-grid img").first();
  await expect.poll(() => firstImage.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
  const exact = await request.get(first.items[0].url);
  expect(exact.status()).toBe(200);
  await page.getByRole("button", { name: "连续预览", exact: true }).click();
  const preview = page.getByRole("region", { name: "连续预览" });
  await expect(preview.getByRole("button", { name: "播放", exact: true })).toBeVisible();
  await page.getByRole("combobox", { name: "每张图片预览时长" }).selectOption("0.5");
  await preview.getByRole("button", { name: "播放", exact: true }).click();
  await expect.poll(() => preview.locator("img.is-current").getAttribute("alt")).not.toContain("预览第 1 张：");
  await preview.getByRole("button", { name: "停止", exact: true }).click();
  await page.getByRole("checkbox", { name: "保留第 1 张图片", exact: true }).click();
  await expect.poll(async () => (await get()).items.filter(item => item.included).length).toBe(first.items.length - 1);
  await page.getByRole("button", { name: "将第 2 张图片向前移动", exact: true }).click();
  await expect.poll(async () => (await get()).items[0].id).toBe(first.items[1].id);
  await page.reload();
  await expect(page.locator(".media-image-grid article")).toHaveCount(first.items.length);
  const sortResponse = page.waitForResponse(response => response.url().endsWith(`${base}/${id}/sort`) && response.request().method() === "POST");
  await page.getByRole("button", { name: "相近排序（试验性）", exact: true }).click();
  expect((await sortResponse).status()).toBe(200);
  await expect(page.getByRole("status").filter({ hasText: "已保存" })).toBeVisible({ timeout: 30_000 });
  const sorted = await get();
  expect(sorted.items.at(-1)!.id).toBe(first.items[0].id);
  expect(sorted.items.at(-1)!.included).toBe(false);
  const downloadEvent = page.waitForEvent("download");
  await page.getByRole("button", { name: /^下载所选 ZIP/ }).click();
  const download = await downloadEvent;
  const zip = info.outputPath("selected.zip");
  await download.saveAs(zip);
  const result = JSON.parse(execFileSync(process.env.CREATOROS_PYTHON ?? "python", ["-c", `
import hashlib,json,sys,zipfile
with zipfile.ZipFile(sys.argv[1]) as z:
    m=json.loads(z.read('manifest.json'))
    print(json.dumps({'manifest':m,'files':[n for n in z.namelist() if n!='manifest.json'],
        'checksums':[hashlib.sha256(z.read(i['filename'])).hexdigest() for i in m['items']]}))
`, zip], { encoding: "utf8" }));
  const selected = sorted.items.filter(item => item.included);
  expect(result.manifest.items.map((item: { id: string }) => item.id)).toEqual(selected.map(item => item.id));
  expect(result.checksums).toEqual(selected.map(item => item.checksum));
  expect(result.manifest.selected_count).toBe(selected.length);
  expect(result.files).toHaveLength(selected.length);
  await page.screenshot({ path: info.outputPath("collection-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(page.getByRole("button", { name: /^下载所选 ZIP/ })).toBeVisible();
  await page.screenshot({ path: info.outputPath("collection-mobile.png"), fullPage: true });
  await page.getByRole("button", { name: "删除文件夹", exact: true }).click();
  await page.getByRole("button", { name: "确认删除文件夹", exact: true }).click();
  await expect(page.locator(".media-detail")).toHaveCount(0);
  expect((await request.get(first.items[0].url)).status()).toBe(404);
  expect(writes.every(path => path.startsWith(base))).toBe(true);
  const protection: Fixture = await request.get("/test/media-fixture").then(r => r.json());
  expect(protection.workers_started).toBe(false);
  expect(protection.source_run_rows_unchanged).toBe(true);
  expect(protection.source_images_unchanged).toBe(true);
  await info.attach("original-data-protection.json", { body: JSON.stringify(protection, null, 2), contentType: "application/json" });
});

test("real HTTP response loss and stale edits do not resend or silently overwrite", async ({ page, request }) => {
  const fixture: Fixture = await request.get("/test/media-fixture").then(r => r.json());
  const base = `/api/creators/${fixture.creator_id}/collections`;
  const created = await request.post(base, { data: { name: "失连与并发验收", run_ids: [fixture.run_ids[0]], request_id: crypto.randomUUID() } }).then(r => r.json()) as Detail;
  await page.goto(`/?creator=${fixture.creator_id}&view=materials&collection=${created.id}`);
  await expect(page.locator(".media-image-grid article")).toHaveCount(created.items.length);
  let patches = 0;
  await page.route(`**${base}/${created.id}`, async route => {
    if (route.request().method() !== "PATCH") return route.continue();
    patches++;
    const saved = await route.fetch();
    expect(saved.status()).toBe(200);
    await route.abort("failed");
  });
  await page.getByRole("checkbox", { name: "保留第 1 张图片", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "请求结果待核对" })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: "保留第 1 张图片", exact: true })).not.toBeChecked();
  expect(patches).toBe(1);
  await page.unroute(`**${base}/${created.id}`);
  const get = () => request.get(`${base}/${created.id}`).then(r => r.json() as Promise<Detail>);
  const current = await get();
  await page.getByRole("button", { name: "改名", exact: true }).click();
  await page.locator(".media-inline-edit").getByLabel("文件夹名称", { exact: true }).fill("过期改名不应保存");
  const external = await request.patch(`${base}/${created.id}`, { data: { expected_version: current.version, name: "另一个窗口已修改" } });
  expect(external.status()).toBe(200);
  await page.getByRole("button", { name: "保存名称", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "文件夹已更新" })).toBeVisible();
  expect((await get()).name).toBe("另一个窗口已修改");
  await page.reload();
  await expect(page.getByRole("heading", { name: "另一个窗口已修改", exact: true })).toBeVisible();
  await request.delete(`${base}/${created.id}?expected_version=${(await get()).version}`, { headers: { "Content-Type": "application/json" } });
  const protection: Fixture = await request.get("/test/media-fixture").then(r => r.json());
  expect(protection.workers_started).toBe(false);
  expect(protection.source_run_rows_unchanged).toBe(true);
  expect(protection.source_images_unchanged).toBe(true);
});
