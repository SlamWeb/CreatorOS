import { expect, test } from "@playwright/test";

test.use({ permissions: ["clipboard-read", "clipboard-write"] });

// Controlled UI projection, not a model Eval. Destination is a real isolated
// Studio Series; only the chat projection is injected to force malformed URLs.
test("host navigation survives model URL errors, copy, reload and real click", async ({ page, request }, info) => {
  const creatorResponse = await request.post("/api/creators", { data: { display_name: "链接隔离验收" } });
  const creator = await creatorResponse.json();
  const seriesResponse = await request.post(`/api/creators/${creator.id}/series`, { data: { name: "链接目的栏目" } });
  expect(seriesResponse.status()).toBe(201);
  const series = await seriesResponse.json();
  const path = `/series/${series.id}`;
  const raw = `状态已核实。\n\n[伪造域名](https://example.com${path}) · [缺斜杠](series/${series.id}) · [错对象](/series/other)\n\n[资料](https://github.com/openai/codex/actions/runs/123)`;
  const doc = { id: "host-links", title: "链接验收", version: 1, status: "idle", error: null,
    scope_kind: "overview", creator_id: null, updated_at: "2026-10-11T00:00:00Z", has_older: false,
    entries: [{ kind: "assistant", text: raw, complete: true, delivery_version: 1,
      links: [{ url: path, label: "查看栏目", source_tool: "list_series_topics" }] }] };
  let posts = 0;
  page.on("request", event => { if (event.method() === "POST") posts++; });
  await page.route("**/api/agent/sessions**", route => route.fulfill({ json:
    new URL(route.request().url()).pathname.endsWith("/host-links") ? doc : { items: [doc] } }));
  await page.goto("/agent?chat=host-links");
  const reply = page.locator(".chat-reply");
  for (const name of ["伪造域名", "缺斜杠", "错对象"]) {
    await expect(reply.getByText(name, { exact: true })).toBeVisible();
    await expect(reply.getByRole("link", { name, exact: true })).toHaveCount(0);
  }
  await expect(reply.getByRole("link", { name: "资料", exact: true })).toHaveAttribute("href", "https://github.com/openai/codex/actions/runs/123");
  const navigation = page.getByRole("navigation", { name: "本轮任务入口" });
  await expect(navigation.getByRole("link", { name: "查看栏目", exact: true })).toHaveAttribute("href", path);
  await reply.getByRole("button", { name: "复制回复原文" }).click();
  expect((await page.evaluate(() => navigator.clipboard.readText())).replace(/\r\n/g, "\n")).toBe(raw);
  await page.screenshot({ path: info.outputPath("links-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await expect(navigation.getByRole("link", { name: "查看栏目", exact: true })).toHaveAttribute("href", path);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("links-mobile.png"), fullPage: true });
  await navigation.getByRole("link", { name: "查看栏目", exact: true }).click();
  await expect(page).toHaveURL(url => url.pathname === "/" && url.searchParams.get("series") === series.id);
  await expect(page.getByRole("heading", { name: "链接目的栏目", exact: true })).toBeVisible();
  expect(posts).toBe(0);
});
