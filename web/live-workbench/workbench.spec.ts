import { expect, test, type Locator } from "@playwright/test";
import { createHash } from "node:crypto";
import { readWorkbenchReport } from "./read-report";

test("real workbench: original GUI → real services → durable evidence → original Eval", async ({ page, request }, info) => {
  const response = await request.get("/__live_eval__/scenario");
  expect(response.ok()).toBe(true);
  const scenario = await response.json() as { case_id: string; run_id: string; query: string;
    creator_id: string; creator_name: string; skill_names: string[]; series_name: string; expected_images: number };
  expect(scenario.case_id).toBe(info.project.name);
  const browser: Record<string, unknown> = { query: scenario.query, session_id: "", completed: false,
    steps: [], session_posts: 0, turn_posts: 0, merge_posts: 0, posts_after_refresh: 0 };
  const steps = browser.steps as string[];
  const errors: string[] = [];
  let refreshing = false;
  page.on("pageerror", error => errors.push(error.message));
  page.on("request", event => {
    const path = new URL(event.url()).pathname;
    if (!["POST", "PUT", "PATCH", "DELETE"].includes(event.method()) || !path.startsWith("/api/")) return;
    if (path === "/api/agent/sessions") browser.session_posts = Number(browser.session_posts) + 1;
    if (path.endsWith("/turns")) browser.turn_posts = Number(browser.turn_posts) + 1;
    if (path === "/api/skill-extractions/merge") browser.merge_posts = Number(browser.merge_posts) + 1;
    if (refreshing) browser.posts_after_refresh = Number(browser.posts_after_refresh) + 1;
  });
  async function imageLoaded(image: Locator) {
    await expect(image).toBeVisible();
    await expect.poll(() => image.evaluate((element: HTMLImageElement) => element.complete && element.naturalWidth > 0 && element.naturalHeight > 0)).toBe(true);
    return image.evaluate((element: HTMLImageElement) => ({ src: element.currentSrc, width: element.naturalWidth, height: element.naturalHeight }));
  }
  async function reload() { refreshing = true; await page.reload(); }
  async function inspectSkill(dialog: Locator, name: string) {
    const catalog = await (await request.get("/api/producer-skills")).json();
    const skill = catalog.items.find((row: { name: string }) => row.name === name);
    expect(skill).toBeTruthy();
    const expected = await (await request.get(`/api/producer-skills/${skill.id}/files/content?path=SKILL.md`)).json();
    await dialog.getByRole("button", { name: "源码", exact: true }).click();
    await expect.poll(() => dialog.locator(".skill-file-source").textContent()).toBe(expected.content);
    const seen = (browser.binding_sources ??= []) as unknown[];
    seen.push({ skill_id: skill.id, name, content: expected.content, sha256: createHash("sha256").update(expected.content).digest("hex") });
  }
  async function readBinding() {
    let opened = 0;
    for (const name of scenario.skill_names) {
      await page.getByRole("button", { name, exact: true }).click();
      const dialog = page.getByRole("dialog", { name, exact: true });
      await expect(dialog).toBeVisible();
      await expect(dialog.locator(".skill-file-markdown")).not.toBeEmpty();
      await inspectSkill(dialog, name);
      await expect(dialog.getByRole("alert")).toHaveCount(0);
      await dialog.getByRole("button", { name: "关闭 Skill 详情" }).click();
      opened++;
    }
    browser.bindings_opened = opened;
    browser.binding_visible = true;
    steps.push("打开当前栏目绑定 Skill 的完整文件卡片");
  }
  async function accountTurn() {
    await page.goto("/");
    await page.getByRole("button", { name: `查看账号 ${scenario.creator_name}` }).click();
    await page.getByRole("button", { name: "打开账号对话" }).click();
    const panel = page.locator(".account-chat-panel");
    await panel.getByRole("textbox", { name: "给 Agent 的消息" }).fill(scenario.query);
    const created = page.waitForResponse(row => row.request().method() === "POST" && new URL(row.url()).pathname === "/api/agent/sessions");
    const sent = page.waitForResponse(row => row.request().method() === "POST" && new URL(row.url()).pathname.endsWith("/turns"));
    await panel.getByRole("button", { name: "发送 ↑" }).click();
    const session = await created;
    expect(session.status()).toBe(201);
    browser.session_id = (await session.json()).id;
    expect((await sent).status()).toBe(202);
    steps.push(`原账号聊天发出一次 query：${scenario.query}`);
    await expect(panel.locator(".agent-composer [role=status]")).toContainText("可以继续对话", { timeout: 240_000 });
    await expect(panel.getByRole("alert")).toHaveCount(0);
    browser.visible_reply = await panel.locator(".chat-answer").last().innerText();
    expect(String(browser.visible_reply).trim()).not.toBe("");
    await panel.getByRole("button", { name: "复制回复原文" }).last().click();
    browser.copied_reply = await page.evaluate(() => navigator.clipboard.readText());
    const checkpoint = await request.post("/__live_eval__/checkpoint", { data: browser });
    expect(checkpoint.ok()).toBe(true);
    await page.screenshot({ path: info.outputPath("account-reply.png"), fullPage: true });
    await reload();
    await page.getByRole("button", { name: "打开账号对话" }).click();
    await expect.poll(() => panel.locator(".chat-answer").last().innerText()).toBe(String(browser.visible_reply));
    browser.restored_reply = await panel.locator(".chat-answer").last().innerText();
    refreshing = false;
    await panel.getByRole("button", { name: "查看回复 Trace" }).last().click();
    const trace = page.getByRole("dialog", { name: "回复 Trace" });
    await expect(trace.getByRole("navigation", { name: "模型请求步骤" }).getByRole("button").first()).toBeVisible();
    await expect(trace.getByRole("tab", { name: "上下文", exact: true })).toBeVisible();
    await expect(trace.getByRole("alert")).toHaveCount(0);
    browser.trace_visible = true;
    await page.getByRole("button", { name: "关闭 Trace" }).click();
    steps.push("完整回复、复制原文、刷新同一会话与 Trace 实际可读");
    const doc = await (await request.get(`/api/agent/sessions/${browser.session_id}`)).json();
    const answer = doc.entries.filter((entry: { kind: string; complete?: boolean }) => entry.kind === "assistant" && entry.complete).at(-1);
    const links = (answer?.links ?? []) as Array<{ url: string; label: string }>;
    const link = links.find(row => new URL(row.url, page.url()).pathname.startsWith(scenario.case_id === "A14" ? "/series/" : "/runs/"));
    expect(link, "必须由实际工具结果投影出可点击的任务入口").toBeTruthy();
    const navigation = panel.locator(".chat-reply").last().getByRole("navigation", { name: "本轮任务入口" });
    const anchor = navigation.getByRole("link").filter({ hasText: link!.label });
    await expect(anchor).toHaveAttribute("href", link!.url);
    await anchor.click();
    browser.delivery_clicked = true;
    browser.delivery_url = page.url();
    const close = page.getByRole("button", { name: "关闭账号对话" });
    if (await close.isVisible()) await close.click();
    return new URL(link!.url, page.url()).pathname.split("/")[2];
  }
  try {
    if (scenario.case_id === "S13") {
      await page.goto("/skills");
      for (const name of scenario.skill_names) {
        await page.locator(".skill-card").filter({ hasText: name }).click();
        const dialog = page.getByRole("dialog", { name, exact: true });
        await expect(dialog.locator(".skill-file-markdown")).not.toBeEmpty();
        await inspectSkill(dialog, name);
        await dialog.getByRole("button", { name: "加入组合", exact: true }).click();
      }
      await page.getByLabel("融合要求（可选）").fill(scenario.query);
      const submitted = page.waitForResponse(row => row.request().method() === "POST" && new URL(row.url()).pathname === "/api/skill-extractions/merge");
      await page.getByRole("button", { name: "生成融合草稿", exact: true }).click();
      const accepted = await submitted;
      expect(accepted.ok()).toBe(true);
      const job = await accepted.json();
      browser.job_id = job.id;
      steps.push("原 Skill 详情加入组合框、点击融合；直调真实 Codex，无 DeepSeek 中间层");
      const detail = page.locator(".extraction-detail");
      let terminal = "";
      await expect.poll(async () => {
        const current = await (await request.get(`/api/skill-extractions/${job.id}`)).json();
        terminal = current.status;
        return terminal;
      }, { timeout: 1_860_000, intervals: [1000, 3000, 5000] }).toMatch(/^(ready|failed|interrupted|cancelled|unknown)$/);
      expect(terminal, "融合真实失败立即保存证据，不等待30分钟再掩盖失败").toBe("ready");
      await expect(detail.getByRole("button", { name: "编辑", exact: true })).toBeEnabled();
      await expect(detail.getByRole("alert")).toHaveCount(0);
      await expect(detail.locator(".extraction-markdown")).not.toBeEmpty();
      browser.draft_visible = true;
      const files = detail.getByRole("navigation", { name: "Skill 文件" }).getByRole("button");
      const assets = files.filter({ hasText: /^assets\/fusion-sources\/[^/]+\/assets\/reference\.png$/ });
      await expect(assets).toHaveCount(2);
      const loaded = [];
      for (let index = 0; index < 2; index++) {
        await assets.nth(index).click();
        loaded.push(await imageLoaded(detail.locator(".extraction-asset-preview")));
      }
      browser.assets_loaded = loaded.length;
      browser.reference_images = loaded;
      await files.filter({ hasText: /^SKILL\.md$/ }).click();
      await detail.getByRole("button", { name: "编辑", exact: true }).click();
      const editor = detail.getByRole("textbox", { name: "编辑 SKILL.md", exact: true });
      const marker = "\n\n保留双语解释，不额外加总标题。\n";
      browser.original_skill_md = await editor.inputValue();
      await editor.fill(String(browser.original_skill_md) + marker);
      const saved = page.waitForResponse(row => row.request().method() === "POST" && new URL(row.url()).pathname.endsWith("/draft"));
      await detail.getByRole("button", { name: "保存草稿", exact: true }).click();
      expect((await saved).ok()).toBe(true);
      await expect(detail.getByRole("button", { name: "保存草稿", exact: true })).toBeDisabled();
      await reload();
      await expect(page).toHaveURL(url => url.searchParams.get("extraction") === job.id);
      await expect(detail.getByRole("button", { name: "编辑", exact: true })).toBeEnabled();
      await detail.getByRole("button", { name: "编辑", exact: true }).click();
      await expect(editor).toHaveValue(String(browser.original_skill_md) + marker);
      refreshing = false;
      browser.edited_and_restored = true;
      steps.push("打开两份命名空间同名资产，编辑草稿后保存并刷新；未入库、未试产");
    } else {
      const id = await accountTurn();
      if (scenario.case_id === "A14") {
        await expect(page).toHaveURL(url => url.pathname === "/" && url.searchParams.get("series") === id);
        await expect(page.getByRole("heading", { name: scenario.series_name, exact: true })).toBeVisible();
        await readBinding();
        await reload();
        await expect(page.getByRole("heading", { name: scenario.series_name, exact: true })).toBeVisible();
        refreshing = false;
      } else {
        await expect(page).toHaveURL(new RegExp(`/runs/${id}$`));
        browser.production_visible_progress = await page.locator("main").innerText();
        let terminal = "";
        await expect.poll(async () => {
          const current = await (await request.get(`/api/runs/${id}`)).json();
          terminal = current.status;
          return terminal;
        }, { timeout: 1_860_000, intervals: [1000, 3000, 5000] }).toMatch(/^(awaiting_approval|failed|interrupted|cancelled|unknown)$/);
        expect(terminal, "生产真实失败立即保存证据，不等待30分钟再掩盖失败").toBe("awaiting_approval");
        await expect(page.locator(".run-workbench .run-title-row .status-pill")).toContainText("待批准");
        const run = await (await request.get(`/api/runs/${id}`)).json();
        const revision = run.revisions.find((row: { revision_number: number }) => row.revision_number === run.active_revision_number);
        expect(revision.cards).toHaveLength(scenario.expected_images);
        const image = page.getByRole("region", { name: "产物图片" }).locator(".carousel-stage img");
        const images = [], hashes = [], prompts = [];
        for (let index = 1; index <= scenario.expected_images; index++) {
          if (index > 1) await page.getByRole("button", { name: "下一张", exact: true }).click();
          await expect(page.locator(".carousel-paging")).toContainText(`${index} / ${scenario.expected_images}`);
          const dimensions = await imageLoaded(image);
          const bytes = await request.get(dimensions.src);
          expect(bytes.ok()).toBe(true);
          images.push(dimensions);
          hashes.push(createHash("sha256").update(await bytes.body()).digest("hex"));
          const evidence = page.locator(`#page-evidence-${index}`);
          if (!(await evidence.isVisible())) await page.getByRole("button", { name: "本页内容与生图 Prompt", exact: true }).click();
          await expect(evidence.locator("pre").last()).not.toBeEmpty();
          const prompt = await evidence.locator("pre").last().textContent();
          expect(prompt).toBe(revision.cards[index - 1].image_prompt);
          prompts.push(prompt);
        }
        browser.images_loaded = images.length;
        browser.image_dimensions = images;
        browser.image_sha256s = hashes;
        browser.prompt_visible = true;
        browser.visible_prompts = prompts;
        await page.getByRole("button", { name: `放大第 ${scenario.expected_images} 张图片` }).click();
        const zoom = page.getByRole("dialog", { name: "放大图片" });
        await imageLoaded(zoom.getByRole("img"));
        await zoom.getByRole("button", { name: "关闭 ×" }).click();
        await page.screenshot({ path: info.outputPath("real-produced-image.png"), fullPage: true });
        await readBinding();
        await reload();
        await imageLoaded(image);
        await expect(page.locator(".run-workbench .run-title-row .status-pill")).toContainText("待批准");
        refreshing = false;
        await page.getByRole("link", { name: "返回栏目" }).click();
        const cover = page.locator("img.content-card-cover").first();
        const coverDimensions = await imageLoaded(cover);
        const coverBytes = await request.get(coverDimensions.src);
        expect(coverBytes.ok()).toBe(true);
        browser.cover_sha256 = createHash("sha256").update(await coverBytes.body()).digest("hex");
        expect(browser.cover_sha256).toBe(hashes[0]);
        browser.cover_loaded = true;
        steps.push(`真实 Run 待批准，${scenario.expected_images} 张新图逐张自然尺寸与磁盘 SHA 交叉验证、Prompt、放大、刷新与栏目封面`);
      }
    }
    browser.completed = true;
    await page.screenshot({ path: info.outputPath("workbench-complete.png"), fullPage: true });
  } catch (error) {
    browser.error = String(error);
    throw error;
  } finally {
    browser.page_errors = errors;
    const finished = await request.post("/__live_eval__/finish", { data: browser });
    expect(finished.status()).toBe(202);
    await expect.poll(async () => (await request.get("/__live_eval__/result")).status(), { timeout: 90_000 }).toBe(200);
    const result = await (await request.get("/__live_eval__/result")).json();
    await info.attach("full-result", { body: JSON.stringify(result, null, 2), contentType: "application/json" });
    // The independent original Eval page is the final read endpoint, not a backend-only test.
    const view: Record<string, unknown> = { run_id: scenario.run_id, completed: false, evidence_loaded: false };
    try {
      Object.assign(view, await readWorkbenchReport(page, request, scenario.case_id, scenario.run_id,
        scenario.case_id === "S13" ? undefined : scenario.query));
      await page.screenshot({ path: info.outputPath("eval-full-evidence.png"), fullPage: true });
    } catch (error) { view.error = String(error); }
    const committed = await request.post("/__live_eval__/view", { data: view });
    expect(committed.ok()).toBe(true);
    const report = await committed.json();
    await info.attach("view-confirmed-result", { body: JSON.stringify(report, null, 2), contentType: "application/json" });
    expect(view.completed).toBe(true);
    expect(report.checks.filter((check: { status: string }) => check.status === "failed")).toEqual([]);
    // Independent linguistic/fusion/image review remains pending, never auto-passed.
    expect(report.auto_status).toBe("needs_review");
  }
});
