import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

async function prepareScopedSwitch(page: Page, request: APIRequestContext) {
  const fixture = await request.post("/test/skill-files", { data: {} }).then(response => response.json());
  const accountResponse = await request.post("/api/creators", { data: { display_name: `切版核对-${crypto.randomUUID()}` } });
  expect(accountResponse.ok(), await accountResponse.text()).toBeTruthy();
  const account = await accountResponse.json();
  for (const skill of [fixture.mind, fixture.production]) {
    const added = await request.post(`/api/creators/${account.id}/skills`, {
      data: { skill_id: skill.id, request_id: crypto.randomUUID() },
    });
    expect(added.ok(), await added.text()).toBeTruthy();
  }
  const seriesResponse = await request.post("/api/series", { data: {
    name: `切版栏目-${crypto.randomUUID()}`, creator_id: account.id, mind_skill_id: fixture.mind.id,
    production_skill_id: fixture.production.id, description: "", audience: "", request_id: crypto.randomUUID(),
  } });
  expect(seriesResponse.ok(), await seriesResponse.text()).toBeTruthy();
  const series = (await seriesResponse.json()).series;
  await page.goto(`/workspace?creator=${account.id}&view=skills`);
  await page.getByRole("region", { name: "账号 Skill 库", exact: true }).getByRole("button", { name: /^inspector-mind/ }).click();
  const card = page.getByRole("dialog", { name: "inspector-mind", exact: true });
  const created = page.waitForResponse(response => response.request().method() === "POST"
    && new URL(response.url()).pathname === `/api/producer-skills/${fixture.mind.id}/versions`);
  await card.getByRole("button", { name: "创建修改候选", exact: true }).click();
  const response = await created;
  expect(response.ok(), await response.text()).toBeTruthy();
  const candidate = await response.json();
  // Real history validates every edition; a long-lived fixture may have many.
  await expect(card.getByRole("combobox", { name: "查看 Skill 版本" })).toHaveValue(candidate.id, { timeout: 20_000 });
  await card.getByRole("button", { name: "栏目版本", exact: true }).click();
  await card.getByRole("combobox", { name: "版本应用栏目" }).selectOption(series.id);
  return { account, series, candidate, card, source: fixture.mind };
}

test("published but unswitched intent continues only after explicit confirmation", async ({ page, request }) => {
  const { account, series, candidate, card } = await prepareScopedSwitch(page, request);
  const payloads: Record<string, unknown>[] = [];
  const urls: URL[] = [];
  page.on("request", item => {
    const url = new URL(item.url());
    if (url.pathname === `/api/series/${series.id}/skill-version` && item.method() === "POST") {
      urls.push(url); payloads.push(item.postDataJSON());
    }
  });
  const armed = await request.post("/test/skill-switch-fault/after", { data: {} });
  expect(armed.ok(), await armed.text()).toBeTruthy();
  page.once("dialog", confirmation => confirmation.accept());
  await card.getByRole("button", { name: "使用这个版本", exact: true }).click();
  await expect(card.getByRole("button", { name: "核对当前状态", exact: true })).toBeVisible();
  expect(payloads).toHaveLength(1);
  const query = new URLSearchParams({ creator_id: account.id, request_id: String(payloads[0].request_id) });
  const before = await request.get(`/api/series/${series.id}/skill-version?${query}`).then(response => response.json());
  expect(before.status).toBe("published_not_switched");
  expect((await request.get(`/api/series/${series.id}`).then(response => response.json())).revision).toBe(series.revision);
  await card.getByRole("button", { name: "核对当前状态", exact: true }).click();
  const resume = card.getByRole("button", { name: "确认继续切换", exact: true });
  await expect(resume).toBeVisible();
  expect(payloads).toHaveLength(1);
  page.once("dialog", confirmation => confirmation.accept());
  await resume.click();
  await expect.poll(() => payloads.length).toBe(2);
  expect(payloads[1]).toEqual(payloads[0]);
  expect(urls.every(url => url.searchParams.get("creator_id") === account.id)).toBeTruthy();
  await expect.poll(async () => (await request.get(`/api/series/${series.id}/skill-version?${query}`).then(response => response.json())).status).toBe("committed");
  const actual = await request.get(`/api/series/${series.id}`).then(response => response.json());
  expect(actual.mind_skill_id).toBe(candidate.id);
  expect(actual.revision).toBe(series.revision + 1);
});

test("prepared intent is read only until the original request is explicitly resumed", async ({ page, request }) => {
  const { account, series, candidate, card } = await prepareScopedSwitch(page, request);
  const payloads: Record<string, unknown>[] = [];
  page.on("request", item => {
    if (new URL(item.url()).pathname === `/api/series/${series.id}/skill-version` && item.method() === "POST") payloads.push(item.postDataJSON());
  });
  expect((await request.post("/test/skill-switch-fault/before", { data: {} })).ok()).toBeTruthy();
  page.once("dialog", confirmation => confirmation.accept());
  await card.getByRole("button", { name: "使用这个版本", exact: true }).click();
  await expect(card.getByRole("button", { name: "核对当前状态", exact: true })).toBeVisible();
  expect(payloads).toHaveLength(1);
  const query = new URLSearchParams({ creator_id: account.id, request_id: String(payloads[0].request_id) });
  expect((await request.get(`/api/series/${series.id}/skill-version?${query}`).then(response => response.json())).status).toBe("prepared");
  await page.reload();
  await page.getByRole("region", { name: "账号 Skill 库", exact: true }).getByRole("button", { name: /^inspector-mind/ }).click();
  const reopened = page.getByRole("dialog", { name: "inspector-mind", exact: true });
  await reopened.getByRole("combobox", { name: "查看 Skill 版本" }).selectOption(candidate.id);
  let corruptOnce = true;
  await page.route(url => url.pathname === `/api/series/${series.id}/skill-version`, async route => {
    if (route.request().method() === "GET" && corruptOnce) {
      corruptOnce = false;
      const response = await route.fetch();
      const body = await response.json();
      return route.fulfill({ response, json: { ...body, skill_id: "wrong-skill" } });
    }
    return route.continue();
  });
  const verify = reopened.getByRole("button", { name: "核对当前状态", exact: true });
  await verify.click();
  await expect(reopened.getByText(/无法确认返回的状态属于本次切换/)).toBeVisible();
  await expect(reopened.getByRole("button", { name: "确认继续切换", exact: true })).toHaveCount(0);
  expect(payloads).toHaveLength(1);
  await verify.click();
  const resume = reopened.getByRole("button", { name: "确认继续切换", exact: true });
  await expect(resume).toBeVisible();
  expect(payloads).toHaveLength(1);
  page.once("dialog", confirmation => confirmation.accept());
  await resume.click();
  await expect.poll(() => payloads.length).toBe(2);
  expect(payloads[1]).toEqual(payloads[0]);
  await expect.poll(async () => (await request.get(`/api/series/${series.id}/skill-version?${query}`).then(response => response.json())).status).toBe("committed");
  const actual = await request.get(`/api/series/${series.id}`).then(response => response.json());
  expect(actual.mind_skill_id).toBe(candidate.id);
  expect(actual.revision).toBe(series.revision + 1);
});

test("a lost successful response is confirmed without repeating the scoped switch", async ({ page, request }) => {
  const { account, series, candidate, card } = await prepareScopedSwitch(page, request);
  const payloads: Record<string, unknown>[] = [];
  await page.route(url => url.pathname === `/api/series/${series.id}/skill-version`, async route => {
    if (route.request().method() !== "POST") return route.continue();
    payloads.push(route.request().postDataJSON());
    const response = await route.fetch();
    expect(response.ok(), await response.text()).toBeTruthy();
    await route.abort("failed");
  });
  page.once("dialog", confirmation => confirmation.accept());
  await card.getByRole("button", { name: "使用这个版本", exact: true }).click();
  await expect(card.getByRole("button", { name: "核对当前状态", exact: true })).toBeVisible();
  expect(payloads).toHaveLength(1);
  const query = new URLSearchParams({ creator_id: account.id, request_id: String(payloads[0].request_id) });
  expect((await request.get(`/api/series/${series.id}/skill-version?${query}`).then(response => response.json())).status).toBe("committed");
  await card.getByRole("button", { name: "核对当前状态", exact: true }).click();
  await expect(card.getByText("已核对：栏目已切换到该版本。", { exact: true })).toBeVisible();
  await expect(card.getByRole("button", { name: "核对当前状态", exact: true })).toHaveCount(0);
  expect(payloads).toHaveLength(1);
  const actual = await request.get(`/api/series/${series.id}`).then(response => response.json());
  expect(actual.mind_skill_id).toBe(candidate.id);
  expect(actual.revision).toBe(series.revision + 1);
});

test("a committed switch is described as historical if the column changed afterward", async ({ page, request }) => {
  const { account, series, candidate, card, source } = await prepareScopedSwitch(page, request);
  const payloads: Record<string, unknown>[] = [];
  await page.route(url => url.pathname === `/api/series/${series.id}/skill-version`, async route => {
    if (route.request().method() !== "POST") return route.continue();
    payloads.push(route.request().postDataJSON());
    const response = await route.fetch();
    expect(response.ok(), await response.text()).toBeTruthy();
    await route.abort("failed");
  });
  page.once("dialog", confirmation => confirmation.accept());
  await card.getByRole("button", { name: "使用这个版本", exact: true }).click();
  await expect(card.getByRole("button", { name: "核对当前状态", exact: true })).toBeVisible();
  const accepted = await request.get(`/api/series/${series.id}`).then(response => response.json());
  expect(accepted.mind_skill_id).toBe(candidate.id);
  const reverted = await request.post(`/api/series/${series.id}/skill-version?creator_id=${account.id}`, { data: {
    skill_id: source.id, expected_digest: source.digest, expected_revision: accepted.revision, request_id: crypto.randomUUID(),
  } });
  expect(reverted.ok(), await reverted.text()).toBeTruthy();
  const query = new URLSearchParams({ creator_id: account.id, request_id: String(payloads[0].request_id) });
  const inspection = await request.get(`/api/series/${series.id}/skill-version?${query}`).then(response => response.json());
  expect(inspection.status).toBe("committed");
  expect(inspection.current_skill_id).toBe(source.id);
  await card.getByRole("button", { name: "核对当前状态", exact: true }).click();
  await expect(card.getByText("已核对：上次切换已完成，栏目当前版本已发生变化。", { exact: true })).toBeVisible();
  expect(payloads).toHaveLength(1);
});

test("a missing old intent and a network error keep the original switch locked", async ({ page, request }) => {
  const { account, series, card } = await prepareScopedSwitch(page, request);
  let writes = 0;
  await page.route(url => url.pathname === `/api/series/${series.id}/skill-version`, async route => {
    if (route.request().method() === "POST") { writes++; return route.abort("failed"); }
    return route.continue();
  });
  page.once("dialog", confirmation => confirmation.accept());
  await card.getByRole("button", { name: "使用这个版本", exact: true }).click();
  const verify = card.getByRole("button", { name: "核对当前状态", exact: true });
  await expect(verify).toBeVisible();
  let failRead = true;
  await page.route(url => url.pathname === `/api/series/${series.id}/skill-version`, async route => {
    if (route.request().method() === "GET" && failRead) {
      failRead = false; return route.fulfill({ status: 503, json: { detail: "受控网络故障" } });
    }
    return route.fallback();
  });
  await verify.click();
  await expect(card.getByText(/受控网络故障/)).toBeVisible();
  await expect(verify).toBeVisible();
  await verify.click();
  await expect(card.getByText(/找不到原请求记录/)).toBeVisible();
  await expect(card.getByRole("button", { name: "确认继续切换", exact: true })).toHaveCount(0);
  await expect(card.getByRole("button", { name: "已核对，解除锁定", exact: true })).toHaveCount(0);
  expect(writes).toBe(1);
  const actual = await request.get(`/api/series/${series.id}`).then(response => response.json());
  expect(actual.creator_id).toBe(account.id);
  expect(actual.revision).toBe(series.revision);
});

test("account scope changes and cross-account reads cannot resume a switch", async ({ page, request }) => {
  const { account, series, card } = await prepareScopedSwitch(page, request);
  const other = await request.post("/api/creators", { data: { display_name: `另一个账号-${crypto.randomUUID()}` } }).then(response => response.json());
  const payloads: Record<string, unknown>[] = [];
  page.on("request", item => {
    if (new URL(item.url()).pathname === `/api/series/${series.id}/skill-version` && item.method() === "POST") payloads.push(item.postDataJSON());
  });
  expect((await request.post("/test/skill-switch-fault/before", { data: {} })).ok()).toBeTruthy();
  page.once("dialog", confirmation => confirmation.accept());
  await card.getByRole("button", { name: "使用这个版本", exact: true }).click();
  await expect(card.getByRole("button", { name: "核对当前状态", exact: true })).toBeVisible();
  const requestId = String(payloads[0].request_id);
  const wrongScope = new URLSearchParams({ creator_id: other.id, request_id: requestId });
  expect((await request.get(`/api/series/${series.id}/skill-version?${wrongScope}`)).status()).toBe(403);
  const changed = await request.post(`/api/creators/${account.id}/skills`, {
    data: { skill_id: "knowledge-to-carousel", request_id: crypto.randomUUID() },
  });
  expect(changed.ok(), await changed.text()).toBeTruthy();
  const ownScope = new URLSearchParams({ creator_id: account.id, request_id: requestId });
  expect((await request.get(`/api/series/${series.id}/skill-version?${ownScope}`).then(response => response.json())).status).toBe("authorization_changed");
  await card.getByRole("button", { name: "核对当前状态", exact: true }).click();
  await expect(card.getByText(/账号对 Skill 的使用范围已变化/)).toBeVisible();
  await expect(card.getByRole("button", { name: "确认继续切换", exact: true })).toHaveCount(0);
  await expect(card.getByRole("button", { name: "已核对，解除锁定", exact: true })).toHaveCount(0);
  expect(payloads).toHaveLength(1);
});

test("a switch rejected after explicit resume remains pending for another read", async ({ page, request }) => {
  const { account, series, card } = await prepareScopedSwitch(page, request);
  const payloads: Record<string, unknown>[] = [];
  page.on("request", item => {
    if (new URL(item.url()).pathname === `/api/series/${series.id}/skill-version` && item.method() === "POST") payloads.push(item.postDataJSON());
  });
  expect((await request.post("/test/skill-switch-fault/before", { data: {} })).ok()).toBeTruthy();
  page.once("dialog", confirmation => confirmation.accept());
  await card.getByRole("button", { name: "使用这个版本", exact: true }).click();
  await card.getByRole("button", { name: "核对当前状态", exact: true }).click();
  const resume = card.getByRole("button", { name: "确认继续切换", exact: true });
  await expect(resume).toBeVisible();
  const renamed = await request.patch(`/api/series/${series.id}`, { data: {
    name: `${series.name}-已变更`, expected_revision: series.revision, request_id: crypto.randomUUID(),
  } });
  expect(renamed.ok(), await renamed.text()).toBeTruthy();
  page.once("dialog", confirmation => confirmation.accept());
  await resume.click();
  await expect.poll(() => payloads.length).toBe(2);
  expect(payloads[1]).toEqual(payloads[0]);
  const verify = card.getByRole("button", { name: "核对当前状态", exact: true });
  await expect(verify).toBeVisible();
  const query = new URLSearchParams({ creator_id: account.id, request_id: String(payloads[0].request_id) });
  expect((await request.get(`/api/series/${series.id}/skill-version?${query}`).then(response => response.json())).status).toBe("binding_changed");
  await verify.click();
  await expect(card.getByText(/栏目配置已变化/)).toBeVisible();
  await expect(card.getByRole("button", { name: "确认继续切换", exact: true })).toHaveCount(0);
  await expect(card.getByRole("button", { name: "已核对，解除锁定", exact: true })).toHaveCount(0);
});
