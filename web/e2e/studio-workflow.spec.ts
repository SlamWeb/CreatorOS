import { expect, test } from "@playwright/test";

test("workspace first use to revision and approval survives refresh", async ({ page }, testInfo) => {
  await page.route(/\/api\/creators\/[^/]+\/tasks(?:\?.*)?$/, route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ items: [{ id: "e2e-task", kind: "research", title: "Creator Routing 选题调研", status: "running", series_id: null, run_id: null, url: null, updated_at: "2026-10-08T08:00:00Z", last_activity_at: "2026-10-08T08:00:00Z" }], summary: { active: 1, awaiting_approval: 0, failed: 0 }, as_of: "2026-10-08T08:00:00Z" }),
  }));
  await page.goto("/");
  await expect(page.getByText("还没有栏目")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("01-first-use.png"), fullPage: true });

  await page.getByRole("button", { name: "+ 新账号" }).click();
  await page.getByLabel("新账号名称").fill("E2E 知识实验室");
  await page.getByLabel("新账号标识").fill("e2e_lab");
  await page.getByRole("button", { name: "创建", exact: true }).click();
  await expect(page.getByRole("heading", { name: "E2E 知识实验室", level: 2 })).toBeVisible();
  await expect(page.getByRole("region", { name: "任务" })).toContainText("Creator Routing 选题调研");
  await expect(page.getByRole("region", { name: "任务" })).toContainText("进行中 1");

  await page.getByRole("button", { name: "在 E2E 知识实验室 下新建栏目" }).click();
  await page.getByLabel("新栏目名称").fill("Agent 每日一题");
  await page.getByRole("button", { name: "创建", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Agent 每日一题", level: 1 })).toBeVisible();

  await page.getByLabel("新选题标题").fill("Agent State 和 Messages 有什么区别？");
  await page.getByRole("button", { name: "添加", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Agent State 和 Messages 有什么区别？" })).toBeVisible();

  // Preview 冲突覆盖保留：一条计划先被别处确认，抽屉内确认应提示过期而不是覆盖。
  const seriesId = new URL(page.url()).searchParams.get("series")!;
  const first = await page.evaluate(async (id) => {
    return fetch("/api/operations/preview", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        request_text: "再添加一条选题", series_id: id,
        plan: { schema_version: 1, operations: [{ action: "add_topics", series_id: id, topics: [{ topic_id: "e2e-extra-topic", title: "什么是 Context 压缩？", source: "manual" }] }] },
      }),
    }).then((response) => response.json());
  }, seriesId);
  await page.evaluate(async (id) => {
    const replacement = await fetch("/api/operations/preview", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        request_text: "模拟另一页面先修改队列", series_id: id,
        plan: { schema_version: 1, operations: [{ action: "add_topics", series_id: id, topics: [{ topic_id: "e2e-stale-winner", title: "另一条抢先写入", source: "manual" }] }] },
      }),
    }).then((response) => response.json());
    await fetch(`/api/operations/${replacement.id}/confirm`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_version: replacement.version, expected_revision: replacement.revision, confirmation_token: replacement.confirmation_token }),
    });
  }, seriesId);
  await page.goto(`/?series=${seriesId}&operation=${first.id}`);
  const dialog = page.getByRole("dialog", { name: "运营指令" });
  await expect(dialog.getByText("等待确认")).toBeVisible();
  await dialog.getByRole("button", { name: "确认写入队列" }).click();
  await expect(dialog.getByText(/计划已在其他页面更新|内容已变化|确认凭证已过期/)).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("02-stale-confirmation.png"), fullPage: true });
  await page.keyboard.press("Escape");

  await page.getByRole("button", { name: "生产", exact: true }).first().click();
  const discussionEntries: Record<string, unknown>[] = [];
  const discussionRequestIds: string[] = [];
  const discussionDigests: string[] = [];
  let discussionPostCount = 0;
  await page.route(/\/api\/runs\/[^/]+\/discussion$/, async route => {
    if (route.request().method() === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: discussionEntries }) });
      return;
    }
    const body = route.request().postDataJSON() as { request_id: string; revision_id: string; artifact_digest: string; message: string };
    discussionPostCount += 1;
    discussionRequestIds.push(body.request_id);
    discussionDigests.push(body.artifact_digest);
    if (body.message.startsWith("确定失败测试")) {
      await route.fulfill({ status: 422, contentType: "application/json", body: JSON.stringify({ error: { message: "受控请求校验失败" } }) });
      return;
    }
    const entry = {
      id: `e2e-discussion-${discussionPostCount}`, request_id: body.request_id, run_id: "e2e-run", revision_id: body.revision_id,
      message: body.message, status: "completed", reply: `我会按已冻结的图片和 Skill 核对：${body.message}`, error: null,
      thread_id: "e2e-discussion-thread", source_thread_id: "e2e-production-thread",
      created_at: "2026-10-08T08:10:00Z", updated_at: "2026-10-08T08:10:05Z",
      events: [{ id: 1, kind: "status", text: "讨论已开始", at: "2026-10-08T08:10:00Z" }, { id: 2, kind: "message", text: "已读取冻结产物。", at: "2026-10-08T08:10:02Z" }],
      context: { revision_number: 1, image_count: 3, images: [{ order: 1, sha256: "0123456789abcdef" }], skills: [{ role: "production", name: "knowledge-to-carousel", digest: "abcdef0123456789" }], includes: ["第 1 版图片", "冻结 Skill"], excludes: ["不修改产物", "不批准或发布"], history_mode: "forked_production" },
    };
    discussionEntries.push(entry);
    if (body.message.startsWith("未知结果测试")) {
      await route.abort("failed");
      return;
    }
    if (body.message.startsWith("响应解析测试")) {
      await route.fulfill({ status: 200, contentType: "application/json", body: "{" });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(entry) });
  });
  await page.getByRole("link", { name: "查看本次运行" }).click();
  await expect(page.getByText(/Codex 正在生产/)).toBeVisible();
  const discussPanel = page.getByRole("region", { name: "第 1 版的 Codex 讨论" });
  await expect(discussPanel.getByLabel("想和 Codex 核对什么？")).toBeDisabled();
  let firstRevisionId = "";
  const firstRunId = new URL(page.url()).pathname.split("/").pop()!;
  const usedSkill = page.getByRole("region", { name: "本次运行使用的 Skill" });
  await expect(usedSkill).toBeVisible();
  await expect(usedSkill).toContainText("Run 冻结摘要");
  await expect(usedSkill).toContainText("编辑只影响新 Run");
  await usedSkill.getByRole("button", { name: "打开当前库版本 ↗" }).click();
  const skillCard = page.getByRole("dialog", { name: "knowledge-to-carousel" });
  await expect(skillCard).toBeVisible();
  await skillCard.getByRole("button", { name: "关闭 Skill 详情" }).click();
  await page.screenshot({ path: testInfo.outputPath("03-producing.png"), fullPage: true });
  await expect(page.getByRole("button", { name: "批准第 1 版" })).toBeVisible({ timeout: 15_000 });
  firstRevisionId = await page.getByLabel("内容版本").inputValue();
  const reviewDigest = await page.evaluate(async ({ runId, revisionId }) => {
    const detail = await fetch(`/api/runs/${runId}`).then(response => response.json());
    return detail.revisions.find((item: { id: string }) => item.id === revisionId)?.review_digest;
  }, { runId: firstRunId, revisionId: firstRevisionId });
  await discussPanel.getByLabel("想和 Codex 核对什么？").fill("请指出这张图里最需要核对的事实，不要修改产物。");
  await discussPanel.getByRole("button", { name: "发送讨论" }).click();
  expect(discussionDigests[0]).toBe(reviewDigest);
  await expect(discussPanel.getByText("我会按已冻结的图片和 Skill 核对：请指出这张图里最需要核对的事实，不要修改产物。" )).toBeVisible();
  await expect(discussPanel.getByText("本次传入什么")).toBeVisible();
  await discussPanel.getByText("本次传入什么").click();
  await expect(discussPanel.getByText("从生产对话分出独立讨论")).toBeVisible();
  await discussPanel.getByText("执行活动 · 2").click();
  await expect(discussPanel.getByText("已读取冻结产物。")).toBeVisible();
  await expect.poll(() => discussionPostCount).toBe(1);
  await page.screenshot({ path: testInfo.outputPath("discussion-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  const discussionWidth = await page.evaluate(() => ({ body: document.body.scrollWidth, viewport: window.innerWidth }));
  expect(discussionWidth.body).toBeLessThanOrEqual(discussionWidth.viewport);
  await page.screenshot({ path: testInfo.outputPath("discussion-mobile.png"), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 900 });

  await discussPanel.getByRole("button", { name: "继续讨论" }).click();
  await discussPanel.getByLabel("想和 Codex 核对什么？").fill("确定失败测试：这次应明确拒绝，不要自动重发。");
  await discussPanel.getByRole("button", { name: "发送讨论" }).click();
  await expect(discussPanel.getByRole("alert")).toContainText("受控请求校验失败");
  await discussPanel.getByRole("button", { name: "编辑消息并新建请求" }).click();
  await expect(discussPanel.getByLabel("想和 Codex 核对什么？")).toHaveValue("确定失败测试：这次应明确拒绝，不要自动重发。");
  await discussPanel.getByLabel("想和 Codex 核对什么？").fill("未知结果测试：请确认服务端是否已经记录。");
  await discussPanel.getByRole("button", { name: "发送讨论" }).click();
  await expect(discussPanel.getByRole("alert")).toContainText("结果可能尚未返回");
  expect(discussionRequestIds[0]).not.toBe(discussionRequestIds[1]);
  expect(discussionRequestIds[1]).not.toBe(discussionRequestIds[2]);
  await discussPanel.getByRole("button", { name: "核对提交状态" }).click();
  await expect(discussPanel.getByRole("button", { name: "继续讨论" })).toBeVisible();
  await expect(discussPanel.getByText("我会按已冻结的图片和 Skill 核对：未知结果测试：请确认服务端是否已经记录。")).toBeVisible();
  await page.reload();
  const recoveredDiscussion = page.getByRole("region", { name: "第 1 版的 Codex 讨论" });
  await expect(recoveredDiscussion.getByRole("button", { name: "继续讨论" })).toBeVisible();
  await expect.poll(() => discussionPostCount).toBe(3);
  await expect(recoveredDiscussion.getByText("我会按已冻结的图片和 Skill 核对：未知结果测试：请确认服务端是否已经记录。")).toBeVisible();
  await recoveredDiscussion.getByRole("button", { name: "继续讨论" }).click();
  await recoveredDiscussion.getByLabel("想和 Codex 核对什么？").fill("响应解析测试：服务端成功但响应损坏，不应重新提交。");
  await recoveredDiscussion.getByRole("button", { name: "发送讨论" }).click();
  await expect(recoveredDiscussion.getByRole("alert")).toContainText("结果可能尚未返回");
  await expect(recoveredDiscussion.getByRole("button", { name: "编辑消息并新建请求" })).toHaveCount(0);
  await recoveredDiscussion.getByRole("button", { name: "核对提交状态" }).click();
  await expect(recoveredDiscussion.getByRole("button", { name: "继续讨论" })).toBeVisible();
  await expect.poll(() => discussionPostCount).toBe(4);

  await page.screenshot({ path: testInfo.outputPath("04-inspector.png"), fullPage: true });

  await page.getByRole("button", { name: "提出返工" }).click();
  await page.getByLabel("告诉生产者要改哪里").fill("第二张换成点餐场景，保留其余结构。");
  await page.getByRole("button", { name: "保存返工要求" }).click();
  await page.getByRole("button", { name: "开始生产" }).click();
  await expect(page.getByRole("button", { name: "批准第 2 版" })).toBeVisible({ timeout: 15_000 });
  const secondDiscussion = page.getByRole("region", { name: "第 2 版的 Codex 讨论" });
  await expect(secondDiscussion).toBeVisible();
  await expect(secondDiscussion.getByText("本版还没有讨论记录。")).toBeVisible();
  await expect(secondDiscussion.getByText("请指出这张图里最需要核对的事实，不要修改产物。")).toHaveCount(0);
  const runId = firstRunId;
  await page.evaluate(url => {
    window.history.pushState({}, "", url);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, `/runs/${runId}?revision=${encodeURIComponent(firstRevisionId)}&discussion=e2e-discussion-1`);
  await expect(page.getByLabel("内容版本")).toHaveValue(firstRevisionId);
  await expect(page.getByRole("region", { name: "第 1 版的 Codex 讨论" }).getByText("请指出这张图里最需要核对的事实，不要修改产物。", { exact: true })).toBeVisible();
  await page.goto(`/runs/${runId}`);
  await expect(page.getByRole("button", { name: "批准第 2 版" })).toBeVisible();
  await page.getByRole("button", { name: "批准第 2 版" }).click();
  await expect(page.getByText("✓ 已批准 · 尚未发布")).toBeVisible();
  await page.reload();
  await expect(page.getByText("✓ 已批准 · 尚未发布")).toBeVisible();

  // Isolated browser test: this link is deliberately not a real platform post.
  await expect(page.getByRole("link", { name: "下载已批准图片包" })).toBeVisible();
  await page.getByLabel("发布后粘贴笔记链接").fill("https://www.xiaohongshu.com/explore/e2e-isolated-example");
  await page.getByRole("button", { name: "登记为已发布" }).click();
  await expect(page.getByRole("link", { name: "打开笔记 ↗" })).toBeVisible();
  await page.getByLabel("阅读").fill("120");
  await page.getByLabel("点赞").fill("8");
  await page.getByRole("button", { name: "保存这次数据" }).click();
  await expect(page.getByText("历史回填 · 1 次")).toBeVisible();
  await page.reload();
  await expect(page.getByRole("link", { name: "打开笔记 ↗" })).toBeVisible();
  await page.getByText("历史回填 · 1 次").click();
  await expect(page.getByText(/阅读 120 · 赞 8/)).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("approved-manual-publication.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  const publicationWidth = await page.evaluate(() => ({ body: document.body.scrollWidth, viewport: window.innerWidth }));
  expect(publicationWidth.body).toBeLessThanOrEqual(publicationWidth.viewport);
  await page.screenshot({ path: testInfo.outputPath("approved-manual-publication-mobile.png"), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 900 });

  await page.goto("/");
  await expect(page.getByRole("complementary", { name: "账号与栏目" }).getByRole("button", { name: "Agent 每日一题" })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("05-workspace-with-data.png"), fullPage: true });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const mobileTree = page.getByRole("button", { name: /账号与栏目/ });
  await expect(mobileTree).toHaveAttribute("aria-expanded", "false");
  await mobileTree.click();
  await expect(page.locator(".workspace-rail")).toHaveClass(/is-open/);
  await expect(page.getByRole("complementary", { name: "账号与栏目" }).getByRole("button", { name: "Agent 每日一题" })).toBeVisible();
  const width = await page.evaluate(() => ({ body: document.body.scrollWidth, viewport: window.innerWidth }));
  expect(width.body).toBeLessThanOrEqual(width.viewport);
  await page.screenshot({ path: testInfo.outputPath("06-mobile-workspace.png"), fullPage: true });

  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    for (const route of ["/", "/skills", "/agent"]) {
      await page.goto(route);
      await expect(page.locator("h1").first()).toBeVisible();
      await expect(page.locator(".studio-shell")).toHaveCSS("background-color", "rgb(255, 255, 255)");
      const overflow = await page.evaluate(() => [...document.querySelectorAll("main *")].filter(el => el.getBoundingClientRect().right > innerWidth + 1).map(el => ({ tag: el.tagName, cls: el.className, right: el.getBoundingClientRect().right })));
      expect(overflow, route).toEqual([]);
    }
  }
  await page.goto("/");
  await page.getByRole("button", { name: "打开账号对话" }).click();
  const accountPanel = page.locator(".account-chat-panel");
  await expect(accountPanel).toBeVisible();
  await expect(accountPanel.getByRole("heading", { name: "账号对话" })).toBeVisible();
  await expect(page).not.toHaveURL(/\/agent/);
  await page.getByRole("button", { name: "关闭账号对话" }).click();

  await page.goto("/agent");
  await page.getByRole("button", { name: "看看我的账号和栏目 ↗" }).click();
  await expect(page.getByLabel("给 Agent 的消息")).toHaveValue("看看我有哪些账号和栏目，先不要生产。");
  await page.getByRole("button", { name: /运营指令/ }).click();
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  // Failure injection verifies readable feedback without making a model call.
  await page.route(/\/api\/agent\/sessions(?:\?.*)?$/, route => route.fulfill({ status: 503, json: { error: { message: "隔离验收：服务暂不可用" } } }));
  await page.reload();
  await expect(page.getByRole("alert")).toContainText("隔离验收：服务暂不可用", { timeout: 15_000 });
  await page.screenshot({ path: testInfo.outputPath("agent-error-mobile.png"), fullPage: true });
});
