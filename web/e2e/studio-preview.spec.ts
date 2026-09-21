import { expect, test } from "@playwright/test";

test("prototype composition stays local and survives reload", async ({ page }) => {
  const apiCalls: string[] = [];
  await page.route("**/api/**", route => { apiCalls.push(route.request().url()); return route.abort(); });
  await page.goto("/studio-preview");
  const create = page.getByRole("button", { name: "创建栏目", exact: true });
  const mind = page.getByRole("button", { name: "知识讲解 · Deep 因果学习路径 · 教学分镜" });
  const production = page.getByRole("button", { name: "小白 角色图解 · 竖版图文" });
  await expect(create).toBeDisabled();
  await mind.click();
  await production.click();
  await create.click();
  await page.keyboard.press("Escape");
  await expect(page.locator(".sp-series")).toHaveCount(0);
  await expect(create).toBeFocused();
  await create.click();
  await page.getByLabel("栏目名称", { exact: true }).fill("Agent 图解");
  await page.getByRole("button", { name: "创建", exact: true }).click();
  await expect(page.getByTestId("unassigned").locator(".sp-series")).toHaveCount(1);

  // Pointer gesture, not synthetic drag/drop events: reproduce the embedded-browser path.
  async function drag(source: ReturnType<typeof page.locator>, target: ReturnType<typeof page.locator>) {
    const a = await source.boundingBox(); const b = await target.boundingBox();
    if (!a || !b) throw new Error("Missing drag target");
    await page.mouse.move(a.x + a.width / 2, a.y + 25);
    await page.mouse.down();
    await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 12 });
    await page.mouse.up();
  }
  await drag(mind, page.getByTestId("slot-production"));
  await expect(page.getByRole("status")).toHaveText("这里需要制作 Skill");
  await expect(create).toBeDisabled();
  await drag(mind, page.getByTestId("slot-mind"));
  await expect(mind).toHaveAttribute("aria-pressed", "true");
  await drag(page.locator(".sp-series"), page.getByTestId("demo-knowledge"));
  await expect(page.getByTestId("demo-knowledge").locator(".sp-series")).toHaveCount(1);
  await page.getByRole("combobox", { name: "分配 Agent 图解" }).selectOption("");
  await expect(page.getByTestId("unassigned").locator(".sp-series")).toHaveCount(1);
  await page.getByRole("button", { name: /Agent 图解 知识讲解/ }).click();
  await page.getByLabel("选题标题").fill("Agent 为什么需要上下文压缩？");
  await page.getByRole("button", { name: "添加选题" }).click();
  await expect(page.getByRole("button", { name: "开始生产" })).toBeDisabled();
  await page.reload();
  await page.getByRole("button", { name: /Agent 图解 知识讲解/ }).click();
  await expect(page.getByText("Agent 为什么需要上下文压缩？")).toBeVisible();
  await page.getByRole("button", { name: "← 返回创作空间" }).click();
  await page.getByRole("button", { name: "＋ 添加 Skill" }).click();
  await page.getByLabel("GitHub 链接").fill("https://example.com/skill");
  await page.getByLabel("Skill 名称").fill("测试方法");
  await page.getByRole("button", { name: "添加示例条目" }).click();
  await expect(page.getByText("请填写 https://github.com/ 开头的链接", { exact: true }).last()).toBeVisible();
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.getByRole("status")).toBeEmpty();
  await expect(page.locator(".sp-skill")).toHaveCount(4);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("button", { name: "＋ 添加 Skill" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(apiCalls).toEqual([]);
});
