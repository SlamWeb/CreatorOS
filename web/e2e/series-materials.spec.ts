import { expect, test } from "@playwright/test";

test("series materials keep old unassigned collections accessible and scope new folders", async ({ page, request }) => {
  async function post(url: string, data: object) {
    const response = await request.post(url, { data });
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  }
  const account = await post("/api/creators", { display_name: `素材栏目-${crypto.randomUUID().slice(0, 8)}` });
  const first = await post(`/api/creators/${account.id}/series`, { name: "栏目甲" });
  const second = await post(`/api/creators/${account.id}/series`, { name: "栏目乙" });
  const base = `/api/creators/${account.id}/collections`;
  await post(base, { name: "旧待整理", request_id: crypto.randomUUID() });
  await post(base, { name: "另一栏目的素材", series_id: second.id, request_id: crypto.randomUUID() });
  await page.goto(`/workspace?creator=${account.id}&series=${first.id}&view=materials`);
  const section = page.getByRole("region", { name: "素材文件夹" });
  await expect(section).toBeVisible();
  await expect(section.getByRole("button", { name: "另一栏目的素材" })).toHaveCount(0);
  await section.getByRole("button", { name: "待整理素材" }).click();
  await expect(section.getByRole("navigation", { name: "待整理素材" }).getByRole("button", { name: "旧待整理" })).toBeVisible();
  await section.getByRole("button", { name: "新建文件夹" }).first().click();
  await section.getByLabel("文件夹名称").fill("栏目甲素材");
  const created = page.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === base);
  await section.getByRole("button", { name: "创建文件夹" }).click();
  const response = await created;
  expect((await response.json()).series_id).toBe(first.id);
  await page.reload();
  await expect(section.getByRole("navigation", { name: "选择素材文件夹" }).getByRole("button", { name: "栏目甲素材" })).toBeVisible();
});
