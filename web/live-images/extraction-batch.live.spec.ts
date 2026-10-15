import { test, expect } from "@playwright/test";
import { writeFileSync } from "node:fs";

// Explicit paid test: only against the isolated server launched by tests.live_chat_images.
test("fifteen references reach one real Codex extraction and all remain in its draft", async ({ page, request }, info) => {
  test.setTimeout(720_000);
  test.skip(!process.env.CREATOROS_BATCH_IMAGES, "Explicit paid batch test only");
  await page.goto("/skills");
  await page.getByRole("button", { name: "从作品提炼", exact: true }).click();
  const images = await page.evaluate(() => Array.from({ length: 15 }, (_, index) => {
    const canvas = document.createElement("canvas"); canvas.width = 320; canvas.height = 400;
    const ctx = canvas.getContext("2d")!;
    ctx.fillStyle = "white"; ctx.fillRect(0, 0, 320, 400);
    ctx.fillStyle = `hsl(${index * 24},65%,45%)`; ctx.fillRect(60, 55, 200, 230);
    ctx.fillStyle = "#161616"; ctx.font = "24px sans-serif";
    ctx.fillText(`SAMPLE ${index + 1}`, 85, 345);
    return canvas.toDataURL("image/png").split(",")[1];
  }));
  await page.getByLabel("参考图片", { exact: true }).setInputFiles(images.map((data, index) => ({
    name: `sample-${String(index + 1).padStart(2, "0")}.png`, mimeType: "image/png", buffer: Buffer.from(data, "base64"),
  })));
  await expect(page.getByLabel("已选图片").getByRole("img")).toHaveCount(15);
  await page.getByLabel("提炼要求", { exact: true }).fill("综合全部15张同类示例，提炼一个可复用的色块标签卡片Skill，用于新颜色、新英文标签，不是复刻这15张。将全部15张原图保留在assets供用户检查。正文简短即可；不要生图、不要试生产、不要入库。");
  const posts: unknown[] = [];
  page.on("request", req => { if (req.method() === "POST" && new URL(req.url()).pathname === "/api/skill-extractions") posts.push(req.postDataJSON()); });
  await page.getByRole("button", { name: "开始提炼", exact: true }).click();
  await expect(page).toHaveURL(/extraction=[a-f0-9]{64}/, { timeout: 60_000 });
  const jobId = new URL(page.url()).searchParams.get("extraction")!;
  let job: any;
  const persist = () => writeFileSync(info.outputPath("batch-evidence.json"), JSON.stringify({ job_id: jobId, posts, job }, null, 2));
  await expect.poll(async () => {
    job = await (await request.get(`/api/skill-extractions/${jobId}`)).json(); persist();
    console.log(JSON.stringify({ job_id: jobId, status: job.status, activity: job.progress?.activity }));
    return ["ready", "failed", "interrupted"].includes(job.status);
  }, { timeout: 600_000, intervals: [30_000] }).toBe(true);
  expect(posts).toHaveLength(1);
  expect(job.uploads).toHaveLength(15); expect(job.status).toBe("ready");
  expect(job.skills).toHaveLength(1); expect(job.saved_skills).toEqual([]);
  expect(job.files.filter((file: {kind: string}) => file.kind === "image")).toHaveLength(15);
  await page.reload();
  await expect(page.getByText("任务状态：待确认")).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Skill 文件" }).getByRole("button")).toHaveCount(16);
  expect(posts).toHaveLength(1);
  persist();
});
