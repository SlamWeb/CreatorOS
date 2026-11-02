import { test, expect } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import path from "node:path";

// Explicit paid test: only against tests.live_batch_images_server with a fresh root.
test("fifteen references reach one real Codex extraction and all remain in its draft", async ({ page, request }, info) => {
  test.setTimeout(720_000);
  expect(process.env.CREATOROS_BATCH_IMAGES_LIVE, "Explicit real Codex authorization required").toBe("1");
  const setup = JSON.parse(readFileSync(path.join(process.env.CREATOROS_BATCH_IMAGES_ROOT!, "batch-setup.json"), "utf8"));
  const fixtureResponse = await request.get("/test/batch/fixture");
  expect(fixtureResponse.ok()).toBeTruthy();
  const fixture = await fixtureResponse.json();
  expect(fixture.fixture_id).toBe(setup.fixture_id);
  expect(fixture.root).toBe(setup.root);
  expect(fixture.live_models).toBe(true);
  expect(fixture.background_services_started).toBe(false);
  expect(fixture.reserved).toBe(false);
  expect(fixture.submitted).toBe(false);
  const reserved = await request.post("/test/batch/reserve", { data: {} });
  expect(reserved.ok(), await reserved.text()).toBeTruthy();
  const reservation = await reserved.json();
  expect(reservation.fixture_id).toBe(setup.fixture_id);
  await page.setExtraHTTPHeaders({ "x-creatoros-batch-token": reservation.token });
  writeFileSync(info.outputPath("reservation.json"), JSON.stringify({ fixture_id: setup.fixture_id, reserved: true }));
  await page.goto("/skills");
  await page.getByRole("button", { name: "从作品提炼", exact: true }).click();
  const extraction = page.getByRole("form", { name: "从作品提炼" });
  const images = await page.evaluate(() => Array.from({ length: 15 }, (_, index) => {
    const canvas = document.createElement("canvas"); canvas.width = 320; canvas.height = 400;
    const ctx = canvas.getContext("2d")!;
    ctx.fillStyle = "white"; ctx.fillRect(0, 0, 320, 400);
    ctx.fillStyle = `hsl(${index * 24},65%,45%)`; ctx.fillRect(60, 55, 200, 230);
    ctx.fillStyle = "#161616"; ctx.font = "24px sans-serif";
    ctx.fillText(`SAMPLE ${index + 1}`, 85, 345);
    return canvas.toDataURL("image/png").split(",")[1];
  }));
  const expectedHashes = images.map(data => createHash("sha256").update(Buffer.from(data, "base64")).digest("hex"));
  const uploadResponses: { id: string }[] = [];
  const uploads: Promise<void>[] = [];
  page.on("response", response => {
    if (response.request().method() === "POST" && new URL(response.url()).pathname === "/api/skill-extractions/uploads")
      uploads.push((async () => { expect(response.status()).toBe(201); uploadResponses.push(await response.json()); })());
  });
  await extraction.getByLabel("参考图片", { exact: true }).setInputFiles(images.map((data, index) => ({
    name: `sample-${String(index + 1).padStart(2, "0")}.png`, mimeType: "image/png", buffer: Buffer.from(data, "base64"),
  })));
  await expect(extraction.getByLabel("已选图片").getByRole("img")).toHaveCount(15);
  await extraction.getByLabel("提炼要求", { exact: true }).fill("综合全部15张同类示例，提炼一个可复用的色块标签卡片Skill，用于新颜色、新英文标签，不是复刻这15张。将全部15张原图保留在assets供用户检查。正文简短即可；不要生图、不要试生产、不要入库。");
  const posts: unknown[] = [];
  page.on("request", req => { if (req.method() === "POST" && new URL(req.url()).pathname === "/api/skill-extractions") posts.push(req.postDataJSON()); });
  // The form uploads its selected files during submit, before creating a Job.
  await extraction.getByRole("button", { name: "开始提炼", exact: true }).click();
  await expect.poll(() => uploads.length, { timeout: 60_000 }).toBe(15);
  await Promise.all(uploads);
  expect(uploadResponses).toHaveLength(15);
  expect(uploadResponses.map(item => item.id).sort()).toEqual([...expectedHashes].sort());
  await expect(page).toHaveURL(/extraction=[a-f0-9]{64}/, { timeout: 60_000 });
  const jobId = new URL(page.url()).searchParams.get("extraction")!;
  let job: any;
  const persist = () => writeFileSync(info.outputPath("batch-evidence.json"), JSON.stringify({ fixture_id: setup.fixture_id,
    job_id: jobId, upload_responses: uploadResponses, expected_hashes: expectedHashes, posts, job }, null, 2));
  await expect.poll(async () => {
    job = await (await request.get(`/api/skill-extractions/${jobId}`)).json(); persist();
    console.log(JSON.stringify({ job_id: jobId, status: job.status, activity: job.progress?.activity }));
    return ["ready", "failed", "interrupted"].includes(job.status);
  }, { timeout: 600_000, intervals: [30_000] }).toBe(true);
  expect(posts).toHaveLength(1);
  expect(job.uploads).toHaveLength(15); expect(job.status).toBe("ready");
  expect(job.uploads.map((item: { id: string }) => item.id).sort()).toEqual([...expectedHashes].sort());
  expect(job.skills).toHaveLength(1); expect(job.saved_skills).toEqual([]);
  expect(job.files.some((file: {path: string}) => file.path === "SKILL.md")).toBe(true);
  expect(job.files.filter((file: {kind: string}) => file.kind === "image")).toHaveLength(15);
  const retainedHashes = await Promise.all(job.files.filter((file: { kind: string }) => file.kind === "image")
    .map(async (file: { role: string; path: string }) => {
      const response = await request.get(`/api/skill-extractions/${jobId}/files`, { params: { role: file.role, path: file.path } });
      expect(response.ok()).toBeTruthy();
      return createHash("sha256").update(await response.body()).digest("hex");
    }));
  expect(retainedHashes.sort()).toEqual([...expectedHashes].sort());
  await page.reload();
  await expect(page.getByText("任务状态：待确认")).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Skill 文件" }).getByRole("button")).toHaveCount(job.files.length);
  expect(posts).toHaveLength(1);
  expect((await (await request.get("/api/skill-extractions")).json()).items.map((item: { id: string }) => item.id)).toEqual([jobId]);
  persist();
});
