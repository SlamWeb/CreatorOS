import { expect, type APIRequestContext, type Locator, type Page, type TestInfo } from "@playwright/test";
import { createHash } from "node:crypto";
import { readTask } from "./read-progress";
import type { SkillExtractionJob } from "../src/api/types";

export type ArtifactScenario = {
  creator_id: string; series_name: string; skill_names: string[];
  reference_path?: string; edit_name?: string; edit_marker?: string; extraction_instruction?: string; audience?: string;
};

// Business writes happen only through the existing UI. The custom endpoint
// captures read-only checkpoints; it cannot extract, edit, save or compose.
export async function artifactJourney(page: Page, request: APIRequestContext, info: TestInfo,
  scenario: ArtifactScenario, browser: Record<string, unknown>,
  reload: () => Promise<void>, endRefresh: () => void,
  imageLoaded: (image: Locator) => Promise<{ src: string; width: number; height: number }>) {
  const steps = browser.steps as string[];
  const receipts = (browser.artifact_write_receipts = []) as unknown[];
  function responseFor(path: string) {
    return page.waitForResponse(row => row.request().method() === "POST" && new URL(row.url()).pathname === path);
  }
  async function receipt(response: Awaited<ReturnType<typeof responseFor>>) {
    receipts.push({ path: new URL(response.url()).pathname, status: response.status(),
      // Omit upload payload; the server hashes its real bytes separately.
      body: new URL(response.url()).pathname.endsWith("/uploads") ? "image bytes omitted" : response.request().postDataJSON() });
    expect(response.ok()).toBe(true);
    return response.json();
  }
  async function stage(name: string, jobId: string) {
    const captured = await request.post("/__live_eval__/artifact-stage", { data: { name, job_id: jobId } });
    expect(captured.ok(), `${name} must be captured without a business mutation`).toBe(true);
  }
  await page.goto("/skills");
  await page.getByRole("button", { name: "从作品提炼", exact: true }).click();
  const newWork = page.getByRole("button", { name: /^新作品/ });
  if ((await newWork.getAttribute("aria-expanded")) !== "true") await newWork.click();
  await page.getByLabel("参考图片", { exact: true }).setInputFiles(scenario.reference_path!);
  await page.getByLabel("提炼方式", { exact: true }).selectOption("single");
  await page.getByLabel("提炼要求", { exact: true }).fill(scenario.extraction_instruction!);
  const uploaded = responseFor("/api/skill-extractions/uploads");
  const submitted = responseFor("/api/skill-extractions");
  await page.getByRole("button", { name: "开始提炼", exact: true }).click();
  const upload = await receipt(await uploaded);
  const job = await receipt(await submitted) as SkillExtractionJob;
  browser.job_id = job.id;
  steps.push("原作品页面上传真实参考图；单一完整Skill提交一次真实Codex提炼");
  const detail = page.locator(".extraction-detail");
  await expect(detail).toBeVisible();
  browser.extraction_visible_progress = await detail.innerText();
  let current: { status: string; files?: unknown } = { status: job.status };
  await expect.poll(async () => {
    current = await readTask(request, `/api/skill-extractions/${job.id}`, browser);
    return current.status;
  }, { timeout: 900_000, intervals: [1000, 3000, 5000] }).toMatch(/^(ready|failed|interrupted|cancelled|unknown)$/);
  expect(current.status, "提炼实际失败就终止并保留首尝试，不能偷偷重试").toBe("ready");
  await expect(detail.getByRole("alert")).toHaveCount(0);
  await expect(detail.locator(".extraction-markdown")).not.toBeEmpty();
  browser.draft_visible = true;
  await stage("draft_ready", job.id);

  const tree = detail.getByRole("navigation", { name: "Skill 文件" });
  expect(Array.isArray(current.files), "完整Job响应应包含文件列表；不能把纯进度投影当Job").toBe(true);
  const assets = (current.files as NonNullable<SkillExtractionJob["files"]>).filter(file => file.kind === "image" && file.path.startsWith("assets/"));
  expect(assets.length).toBeGreaterThan(0);
  for (const asset of assets) {
    await tree.getByRole("button", { name: asset.path, exact: true }).click();
    const loaded = await imageLoaded(detail.locator(".extraction-asset-preview"));
    const bytes = await request.get(loaded.src);
    expect(bytes.ok()).toBe(true);
    const sha = createHash("sha256").update(await bytes.body()).digest("hex");
    const loadedAssets = (browser.reference_images ??= []) as unknown[];
    loadedAssets.push({ ...loaded, path: asset.path, sha256: sha });
  }
  browser.reference_loaded = true;
  // This case supplies exactly one reference; additional generated assets are
  // allowed but cannot substitute for the uploaded image in the grader.
  const original = (browser.reference_images as Array<{ sha256: string }>).find(image => image.sha256 === upload.id);
  expect(original, "必须真正打开上传的参考作品，而不是其他生成资产").toBeTruthy();
  browser.reference_sha256 = original!.sha256;
  await tree.getByRole("button", { name: "SKILL.md", exact: true }).click();
  await detail.getByRole("button", { name: "编辑", exact: true }).click();
  const editor = detail.getByRole("textbox", { name: "编辑 SKILL.md", exact: true });
  browser.original_skill_md = await editor.inputValue();
  await detail.getByLabel("完整 Skill Frontmatter 名称", { exact: true }).fill(scenario.edit_name!);
  await editor.fill((await editor.inputValue()) + `\n\n${scenario.edit_marker}\n`);
  browser.edited_skill_md = await editor.inputValue();
  await expect(detail.getByRole("button", { name: "确认加入 Skill 库", exact: true })).toBeDisabled();
  const edited = responseFor(`/api/skill-extractions/${job.id}/draft`);
  await detail.getByRole("button", { name: "保存草稿", exact: true }).click();
  await receipt(await edited);
  await expect(detail.getByRole("button", { name: "保存草稿", exact: true })).toBeDisabled();
  await reload();
  await expect(page).toHaveURL(url => url.searchParams.get("extraction") === job.id);
  await expect(detail.getByRole("button", { name: "编辑", exact: true })).toBeEnabled();
  await detail.getByRole("button", { name: "编辑", exact: true }).click();
  await expect(editor).toHaveValue(String(browser.edited_skill_md));
  endRefresh();
  browser.edited_and_restored = true;
  await stage("draft_edited", job.id);
  await page.screenshot({ path: info.outputPath("artifact-reviewed-draft.png"), fullPage: true });
  steps.push("查看真实assets与完整SKILL.md，修改名称和双语版式要求，保存并刷新核对，仍未入库");

  const adopted = responseFor(`/api/skill-extractions/${job.id}/save`);
  await detail.getByRole("button", { name: "确认加入 Skill 库", exact: true }).click();
  const saved = await receipt(await adopted);
  expect(saved.saved_skills).toHaveLength(1);
  await expect(detail).toContainText("已入库");
  await stage("adopted", job.id);
  await reload();
  await expect(detail).toContainText("已入库");
  endRefresh();
  await detail.getByRole("button", { name: `用「${saved.saved_skills[0].name}」创建栏目`, exact: true }).click();
  const composer = page.getByRole("region", { name: "组合栏目", exact: true });
  await composer.getByLabel("栏目名称", { exact: true }).fill(scenario.series_name);
  await composer.getByLabel("栏目定位", { exact: true }).fill("用情境漫画区分常用英语词语");
  await composer.getByLabel("目标受众", { exact: true }).fill(scenario.audience!);
  await composer.getByLabel("归属账号", { exact: true }).selectOption(scenario.creator_id);
  const created = responseFor("/api/series");
  await composer.getByRole("button", { name: "创建栏目", exact: true }).click();
  const column = await receipt(await created);
  browser.created_series_id = column.series.id;
  scenario.skill_names = [saved.saved_skills[0].name];
  await stage("series_created", job.id);
  steps.push("显式确认审核版入库；使用该Skill在原组合框创建当前账号栏目");
}
